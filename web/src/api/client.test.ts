import { describe, expect, it, vi } from "vitest";
import { HttpRunClient, type EventSourceLike } from "./client";
import { mockRecording } from "../fixtures/cargo";
import type { RunObserver } from "./types";

const recording = mockRecording("governed", "warm");
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function observer(): RunObserver {
  return {
    onConnection: vi.fn(),
    onRun: vi.fn(),
    onEvents: vi.fn(),
    onResult: vi.fn(),
  };
}
class Source implements EventSourceLike {
  readyState = 1;
  listeners: Record<string, EventListener[]> = {};
  closed = false;
  addEventListener(type: string, listener: EventListener) {
    (this.listeners[type] ??= []).push(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data?: unknown) {
    const event =
      type === "run_event"
        ? new MessageEvent(type, { data: JSON.stringify(data) })
        : new Event(type);
    this.listeners[type]?.forEach((listener) => listener(event));
  }
}

describe("live execution transport", () => {
  it("subscribes before completion, receives ordered SSE and closes on terminal", async () => {
    const source = new Source();
    let done = false;
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      if (String(url).endsWith("/runs/async"))
        return response(
          { run_id: recording.result.run_id, status: "running" },
          202,
        );
      return response({
        ...recording.result,
        task: recording.task,
        result: done ? recording.result : null,
        status: done ? "completed" : "running",
      });
    });
    const obs = observer();
    const client = new HttpRunClient({
      fetch: fetcher as typeof fetch,
      eventSource: () => source,
    });
    const pending = client.execute(
      { task: recording.task },
      obs,
      new AbortController().signal,
    );
    await vi.waitFor(() => expect(source.listeners.run_event).toBeDefined());
    expect(obs.onResult).not.toHaveBeenCalled();
    source.emit("open");
    source.emit("run_event", recording.events[0]);
    done = true;
    source.emit("run_event", recording.events.at(-1));
    expect(await pending).toEqual(recording.result);
    expect(source.closed).toBe(true);
    expect(obs.onEvents).toHaveBeenCalledWith([recording.events[0]]);
  });

  it("allows native SSE reconnection and accepts duplicate delivery safely upstream", async () => {
    const source = new Source();
    let finished = false;
    const fetcher = vi.fn(async (url: string | URL | Request) =>
      String(url).endsWith("/runs/async")
        ? response({ run_id: recording.result.run_id, status: "running" }, 202)
        : response({
            ...recording.result,
            task: recording.task,
            result: finished ? recording.result : null,
          }),
    );
    const obs = observer();
    const pending = new HttpRunClient({
      fetch: fetcher as typeof fetch,
      eventSource: () => source,
    }).execute({ task: recording.task }, obs, new AbortController().signal);
    await vi.waitFor(() => expect(source.listeners.open).toBeDefined());
    source.emit("open");
    source.emit("run_event", recording.events[0]);
    source.emit("error");
    await new Promise((resolve) => setTimeout(resolve, 5));
    source.emit("open");
    source.emit("run_event", recording.events[0]);
    finished = true;
    source.emit("run_event", recording.events.at(-1));
    await pending;
    expect(obs.onConnection).toHaveBeenCalledWith("reconnecting");
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).endsWith("/runs/async")),
    ).toHaveLength(1);
  });

  it("falls back to cursor polling when an SSE connection cannot open", async () => {
    const source = new Source();
    const fetcher = vi.fn(async (url: string | URL | Request) => {
      const path = String(url);
      if (path.endsWith("/runs/async"))
        return response(
          { run_id: recording.result.run_id, status: "running" },
          202,
        );
      if (path.includes("/events?"))
        return response(
          path.includes("after_sequence=-1") ? recording.events : [],
        );
      return response({
        ...recording.result,
        task: recording.task,
        result: recording.result,
      });
    });
    const obs = observer();
    await new HttpRunClient({
      fetch: fetcher as typeof fetch,
      eventSource: () => source,
      streamTimeoutMs: 5,
      pollIntervalMs: 1,
    }).execute({ task: recording.task }, obs, new AbortController().signal);
    expect(obs.onConnection).toHaveBeenCalledWith("polling");
    expect(source.closed).toBe(true);
  });

  it("falls back from an unsupported async endpoint and discovers a synchronous run", async () => {
    let started = false;
    let complete = false;
    const fetcher = vi.fn(
      async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url);
        if (path.endsWith("/runs/async"))
          return response({ detail: "Not found" }, 404);
        if (path.endsWith("/runs") && init?.method === "POST") {
          started = true;
          await new Promise((resolve) => setTimeout(resolve, 30));
          complete = true;
          return response(recording.result);
        }
        if (path.includes("/runs?"))
          return response(
            started ? [{ ...recording.result, status: "running" }] : [],
          );
        if (path.includes("/events?"))
          return response(
            path.includes("after_sequence=-1") ? recording.events : [],
          );
        return response({
          ...recording.result,
          task: recording.task,
          result: complete ? recording.result : null,
        });
      },
    );
    const obs = observer();
    const result = await new HttpRunClient({
      fetch: fetcher as typeof fetch,
      pollIntervalMs: 1,
    }).execute({ task: recording.task }, obs, new AbortController().signal);
    expect(result).toEqual(recording.result);
    expect(
      fetcher.mock.calls.filter(
        ([url, init]) =>
          String(url).endsWith("/runs") && init?.method === "POST",
      ),
    ).toHaveLength(1);
    expect(obs.onEvents).toHaveBeenCalledWith(recording.events);
  });

  it("never retries a possibly dispatched execution after a 5xx", async () => {
    const fetcher = vi.fn(async () =>
      response({ detail: "Unknown outcome" }, 500),
    );
    await expect(
      new HttpRunClient({ fetch: fetcher as typeof fetch }).execute(
        { task: recording.task },
        observer(),
        new AbortController().signal,
      ),
    ).rejects.toThrow("Unknown outcome");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats an HTTP-200 approval outcome as awaiting approval", async () => {
    const approval = mockRecording("governed", "approval");
    const fetcher = vi.fn(async (url: string | URL | Request) =>
      String(url).endsWith("/runs/async")
        ? response(approval.result)
        : response(approval.events),
    );
    const obs = observer();
    const result = await new HttpRunClient({
      fetch: fetcher as typeof fetch,
    }).execute({ task: approval.task }, obs, new AbortController().signal);
    expect(result.status).toBe("awaiting_approval");
    expect(obs.onResult).toHaveBeenCalledWith(approval.result);
  });

  it("submits only the approval decision, without client identity or privileges", async () => {
    const fetcher = vi.fn(
      async (_url: string | URL | Request, _init?: RequestInit) =>
        response({ approval_id: "approval-1", status: "approved" }),
    );
    await new HttpRunClient({ fetch: fetcher as typeof fetch }).decideApproval(
      "approval-1",
      true,
      "Reviewed",
    );
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toEqual({
      approved: true,
      reason: "Reviewed",
    });
  });
});
