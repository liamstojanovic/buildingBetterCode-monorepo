import {
  isTerminalEvent,
  parseRunEvent,
  type ApprovalDetail,
  type Connection,
  type RunClient,
  type RunDetail,
  type RunEvent,
  type RunObserver,
  type RunResult,
  type RunSummary,
  type StartRunRequest,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted)
      return reject(
        signal.reason ?? new DOMException("Stopped watching", "AbortError"),
      );
    const abort = () => {
      clearTimeout(timer);
      reject(
        signal.reason ?? new DOMException("Stopped watching", "AbortError"),
      );
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export interface EventSourceLike {
  readyState: number;
  addEventListener: (type: string, listener: EventListener) => void;
  close: () => void;
}
export interface ClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
  eventSource?: (url: string) => EventSourceLike;
  pollIntervalMs?: number;
  streamTimeoutMs?: number;
  asyncStartPath?: string;
}

export class HttpRunClient implements RunClient {
  private baseUrl: string;
  private fetcher: typeof fetch;
  private source: ClientOptions["eventSource"];
  private pollMs: number;
  private streamTimeoutMs: number;
  private asyncStartPath: string;
  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "/api").replace(/\/$/, "");
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.source =
      options.eventSource ??
      (typeof EventSource === "undefined"
        ? undefined
        : (url) => new EventSource(url));
    this.pollMs = options.pollIntervalMs ?? 300;
    this.streamTimeoutMs = options.streamTimeoutMs ?? 2500;
    this.asyncStartPath = options.asyncStartPath ?? "/runs/async";
  }

  private async json<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.fetcher(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      const text = await response.text();
      let detail = text;
      try {
        const body = JSON.parse(text);
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail ?? body);
      } catch {
        /* Plain-text proxy errors remain readable. */
      }
      throw new ApiError(detail || `HTTP ${response.status}`, response.status);
    }
    return response.json() as Promise<T>;
  }

  getRun = (id: string, signal?: AbortSignal) =>
    this.json<RunDetail>(`/runs/${encodeURIComponent(id)}`, { signal });
  listRuns = (signal?: AbortSignal) =>
    this.json<RunSummary[]>("/runs?limit=100", { signal });
  getApproval = (id: string) =>
    this.json<ApprovalDetail>(`/approvals/${encodeURIComponent(id)}`);
  decideApproval = (id: string, approved: boolean, reason?: string) =>
    this.json<ApprovalDetail>(`/approvals/${encodeURIComponent(id)}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, reason }),
    });

  private post(request: StartRunRequest, path: string, signal: AbortSignal) {
    return this.json<RunResult | { run_id: string; status: "running" }>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  }

  async execute(
    request: StartRunRequest,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    observer.onConnection("connecting");
    let started: RunResult | { run_id: string; status: "running" };
    try {
      started = await this.post(request, this.asyncStartPath, signal);
    } catch (error) {
      // Only an explicit unsupported endpoint can fall back to a second POST.
      // A timeout/5xx may mean execution started; never redispatch it automatically.
      if (!(error instanceof ApiError) || ![404, 405].includes(error.status))
        throw error;
      return this.legacy(request, observer, signal);
    }
    if ("finished_at" in started) return this.finish(started, observer, signal);
    if (typeof started.run_id !== "string" || started.status !== "running")
      throw new Error(
        "Invalid async-start response; execution status is unknown.",
      );
    const detail = await this.getRun(started.run_id, signal);
    observer.onRun(detail);
    if (this.source) {
      try {
        return await this.stream(started.run_id, observer, signal);
      } catch (error) {
        if (signal.aborted) throw error;
      }
    }
    return this.poll(started.run_id, observer, signal);
  }

  private async legacy(
    request: StartRunRequest,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    const existing = new Set(
      (await this.listRuns(signal)).map((run) => run.run_id),
    );
    observer.onConnection("polling");
    let result: RunResult | undefined;
    let failure: unknown;
    let settled = false;
    const pending = this.post(request, "/runs", signal)
      .then((value) => {
        if (!("finished_at" in value))
          throw new Error(
            "Synchronous endpoint returned an unsupported response.",
          );
        result = value;
      })
      .catch((error: unknown) => {
        failure = error;
      })
      .finally(() => {
        settled = true;
      });
    let runId: string | undefined;
    while (!settled && !runId) {
      try {
        const candidates = (await this.listRuns(signal)).filter(
          (run) =>
            run.task_id === request.task.task_id &&
            run.mode === (request.mode ?? "governed") &&
            !existing.has(run.run_id),
        );
        if (candidates.length === 1) {
          runId = candidates[0].run_id;
          observer.onRun(candidates[0]);
        }
      } catch (error) {
        if (signal.aborted) throw error;
        observer.onConnection("reconnecting");
      }
      if (!settled && !runId) await delay(this.pollMs, signal);
    }
    if (runId) {
      // Consume the POST rejection even when observation completes first.
      const observed = await this.poll(runId, observer, signal);
      void pending;
      return observed;
    }
    await pending;
    if (failure) throw failure;
    if (!result)
      throw new Error(
        "Execution status is unknown. Check run history before retrying.",
      );
    return this.finish(result, observer, signal);
  }

  private async events(
    id: string,
    after: number,
    signal: AbortSignal,
  ): Promise<RunEvent[]> {
    const all: RunEvent[] = [];
    let cursor = after;
    for (;;) {
      const page = await this.json<unknown[]>(
        `/runs/${encodeURIComponent(id)}/events?after_sequence=${cursor}&limit=1000`,
        { signal },
      );
      const events = page
        .map((event) => parseRunEvent(event, id))
        .filter((event) => event.sequence > cursor);
      all.push(...events);
      if (page.length < 1000) return all;
      if (!events.length) throw new Error("Event cursor did not advance");
      cursor = Math.max(...events.map((event) => event.sequence));
    }
  }

  private async finish(
    result: RunResult,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    observer.onRun({ ...result, status: result.status });
    observer.onEvents(await this.events(result.run_id, -1, signal));
    observer.onResult(result);
    observer.onConnection("complete");
    return result;
  }

  private async poll(
    id: string,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    let cursor = -1;
    let failures = 0;
    for (;;) {
      if (signal.aborted) throw signal.reason;
      try {
        const events = await this.events(id, cursor, signal);
        if (events.length) {
          observer.onEvents(events);
          cursor = Math.max(cursor, ...events.map((event) => event.sequence));
        }
        const detail = await this.getRun(id, signal);
        observer.onRun(detail);
        observer.onConnection("polling");
        failures = 0;
        if (detail.result) {
          // The result may commit between the event read and the detail read.
          observer.onEvents(await this.events(id, cursor, signal));
          observer.onResult(detail.result);
          observer.onConnection("complete");
          return detail.result;
        }
      } catch (error) {
        if (
          signal.aborted ||
          (error instanceof ApiError && [403, 404, 422].includes(error.status))
        )
          throw error;
        observer.onConnection("reconnecting");
        failures += 1;
        if (failures >= 6)
          throw new Error(
            "Connection lost. Execution may still be running; inspect history before retrying.",
          );
      }
      await delay(Math.min(this.pollMs * 2 ** failures, 5000), signal);
    }
  }

  private stream(
    id: string,
    observer: RunObserver,
    signal: AbortSignal,
  ): Promise<RunResult> {
    return new Promise((resolve, reject) => {
      const source = this.source!(
        `${this.baseUrl}/runs/${encodeURIComponent(id)}/events/stream?after_sequence=-1`,
      );
      let finishing = false;
      let timer: ReturnType<typeof setTimeout>;
      const cleanup = () => {
        clearTimeout(timer);
        source.close();
        signal.removeEventListener("abort", abort);
      };
      const fail = () => {
        cleanup();
        reject(new Error("SSE unavailable; switching to ordered polling."));
      };
      const armTimer = () => {
        clearTimeout(timer);
        timer = setTimeout(fail, this.streamTimeoutMs);
      };
      const abort = () => {
        cleanup();
        reject(signal.reason);
      };
      const finish = async () => {
        if (finishing) return;
        finishing = true;
        try {
          const detail = await this.getRun(id, signal);
          if (!detail.result) {
            finishing = false;
            armTimer();
            return;
          }
          cleanup();
          observer.onResult(detail.result);
          observer.onConnection("complete");
          resolve(detail.result);
        } catch {
          fail();
        }
      };
      signal.addEventListener("abort", abort, { once: true });
      source.addEventListener("open", () => {
        clearTimeout(timer);
        observer.onConnection("sse");
      });
      source.addEventListener("run_event", ((message: MessageEvent<string>) => {
        try {
          const event = parseRunEvent(JSON.parse(message.data), id);
          observer.onEvents([event]);
          if (isTerminalEvent(event)) void finish();
        } catch (error) {
          cleanup();
          reject(error);
        }
      }) as EventListener);
      source.addEventListener("error", () => {
        observer.onConnection("reconnecting");
        // Native EventSource reconnects with Last-Event-ID. Give it a bounded
        // recovery window before falling back to the durable event cursor API.
        armTimer();
        void finish();
      });
      armTimer();
      if (signal.aborted) abort();
    });
  }
}
