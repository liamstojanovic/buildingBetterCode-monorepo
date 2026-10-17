import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowRight, ChevronLeft, X } from "lucide-react";

export const demoTourSteps = ["workflow", "repeat", "savings", "view"] as const;
export type DemoTourStep = (typeof demoTourSteps)[number];
const copy: Record<
  DemoTourStep,
  { title: string; label: string; body: string; selector: string }
> = {
  workflow: {
    label: "THE WORK",
    title: "Follow the work through the swarm.",
    body: "The job splits into document reads, checks, and a handoff. Novel tasks execute normally. Compare the same job in both execution paths.",
    selector: '[data-demo-focus="workflow"]',
  },
  repeat: {
    label: "THE REPEAT",
    title: "Same task. A different cost.",
    body: "Run this packet again. The baseline repeats the work while Canopy reuses eligible results. We’ll select the repeat scenario for you.",
    selector: '[data-demo-focus="repeat"]',
  },
  savings: {
    label: "THE DIFFERENCE",
    title: "Read the cost of the repeat.",
    body: "Compare time and tokens, then inspect the provider cards below. Estimates use verified text rates when available; unverified rates are omitted. Modeled JeV tokens are separate from measured local JEV results.",
    selector: '[data-demo-focus="metrics"]',
  },
  view: {
    label: "UNDER THE HOOD",
    title: "Inspect the execution tree.",
    body: "Switch between the workflow and terminal tree. Your comparison stays intact, and selecting a task opens its evidence and execution details.",
    selector: '[data-demo-focus="view"]',
  },
};
interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string;
}
const focusable =
  'button:not(:disabled), a[href], select:not(:disabled), input:not(:disabled), [tabindex="0"]';

export function DemoTour({
  step,
  onStep,
  onRun,
  onClose,
}: {
  step: DemoTourStep;
  onStep: (step: DemoTourStep) => void;
  onRun: () => void;
  onClose: () => void;
}) {
  const card = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [geometry, setGeometry] = useState<{
    rects: Rect[];
    width: number;
    height: number;
    cardHeight: number;
  }>({
    rects: [],
    width: window.innerWidth,
    height: window.innerHeight,
    cardHeight: 240,
  });
  const maskId = useId().replace(/:/g, "");
  const description = copy[step];
  const index = demoTourSteps.indexOf(step);

  useEffect(() => {
    previousFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    return () => {
      if (previousFocus.current?.isConnected)
        previousFocus.current.focus({ preventScroll: true });
    };
  }, []);

  useLayoutEffect(() => {
    const targets = [
      ...document.querySelectorAll<HTMLElement>(description.selector),
    ].filter((element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.width > 0 && bounds.height > 0;
    });
    // Align the highlighted content near the top, leaving room for its explanation.
    const anchor =
      step === "savings"
        ? targets[0]?.closest(".run-lane")?.querySelector(".lane-heading")
        : targets[0];
    anchor?.scrollIntoView?.({ block: "start", behavior: "instant" });
    let frame = 0;
    const measure = () => {
      const width = document.documentElement.clientWidth || window.innerWidth;
      const height = window.innerHeight;
      const rects = targets
        .map((element) => {
          const r = element.getBoundingClientRect();
          const lane = element.closest(".run-lane");
          const heading =
            step === "savings"
              ? lane?.querySelector(".lane-heading")?.getBoundingClientRect()
              : null;
          const x = Math.max(6, r.left - 6);
          const y = Math.max(6, (heading?.top ?? r.top) - 6);
          return {
            x,
            y,
            width: Math.max(0, Math.min(width - 6, r.right + 6) - x),
            height: Math.max(0, Math.min(height - 6, r.bottom + 6) - y),
            label:
              step === "workflow"
                ? (lane?.querySelector("h3")?.textContent ?? undefined)
                : undefined,
          };
        })
        .filter((r) => r.width > 0 && r.height > 0);
      setGeometry({
        rects,
        width,
        height,
        cardHeight: card.current?.offsetHeight || 240,
      });
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(schedule);
    targets.forEach((target) => observer?.observe(target));
    if (card.current) observer?.observe(card.current);
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
    measure();
    card.current?.focus({ preventScroll: true });
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
    };
  }, [description.selector]);

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key !== "Tab") return;
      const targets = [
        ...document.querySelectorAll<HTMLElement>(description.selector),
      ].filter((element) => element.getBoundingClientRect().width > 0);
      const allowed = [
        ...targets.flatMap((target) => [
          ...(target.matches(focusable) ? [target] : []),
          ...target.querySelectorAll<HTMLElement>(focusable),
        ]),
        ...(card.current?.querySelectorAll<HTMLElement>(focusable) ?? []),
      ].filter((element) => element.getBoundingClientRect().width > 0);
      if (!allowed.length) return;
      const current = allowed.indexOf(document.activeElement as HTMLElement);
      const next =
        current < 0
          ? event.shiftKey
            ? allowed.length - 1
            : 0
          : (current + (event.shiftKey ? -1 : 1) + allowed.length) %
            allowed.length;
      event.preventDefault();
      allowed[next].focus();
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, [description.selector, onClose]);

  const { rects, width, height, cardHeight } = geometry;
  const cardWidth = Math.min(360, width - 32);
  const bounds = rects.length
    ? {
        left: Math.min(...rects.map((r) => r.x)),
        top: Math.min(...rects.map((r) => r.y)),
        right: Math.max(...rects.map((r) => r.x + r.width)),
        bottom: Math.max(...rects.map((r) => r.y + r.height)),
      }
    : null;
  const left = Math.max(
    16,
    Math.min(
      width - cardWidth - 16,
      step === "repeat" && bounds
        ? bounds.right - cardWidth
        : (bounds?.left ?? 16),
    ),
  );
  const fitsBelow = bounds && bounds.bottom + cardHeight + 30 <= height;
  const fitsAbove = bounds && bounds.top - cardHeight - 14 >= 16;
  const top = fitsBelow
    ? bounds.bottom + 14
    : fitsAbove
      ? bounds.top - cardHeight - 14
      : Math.max(16, height - cardHeight - 16);
  const next = () => {
    if (step === "repeat") onRun();
    else if (index === demoTourSteps.length - 1) onClose();
    else onStep(demoTourSteps[index + 1]);
  };
  const guards = bounds
    ? [
        { left: 0, top: 0, width, height: bounds.top },
        {
          left: 0,
          top: bounds.bottom,
          width,
          height: Math.max(0, height - bounds.bottom),
        },
        {
          left: 0,
          top: bounds.top,
          width: bounds.left,
          height: bounds.bottom - bounds.top,
        },
        {
          left: bounds.right,
          top: bounds.top,
          width: Math.max(0, width - bounds.right),
          height: bounds.bottom - bounds.top,
        },
      ]
    : [{ left: 0, top: 0, width, height }];

  return createPortal(
    <>
      {guards.map((style, i) => (
        <div
          key={i}
          className="demo-tour-guard"
          style={style}
          aria-hidden="true"
          onClick={onClose}
        />
      ))}
      <svg
        className="demo-tour-overlay"
        aria-hidden="true"
        viewBox={`0 0 ${width} ${height}`}
      >
        <defs>
          <mask id={maskId}>
            <rect width={width} height={height} fill="white" />
            {rects.map((r, i) => (
              <rect key={i} {...r} rx="12" fill="black" />
            ))}
          </mask>
        </defs>
        <rect
          width={width}
          height={height}
          fill="var(--tour-scrim)"
          mask={`url(#${maskId})`}
        />
        {rects.map((r, i) => (
          <rect key={i} {...r} rx="12" className="demo-tour-outline" />
        ))}
      </svg>
      {rects.map(
        (r, i) =>
          r.label && (
            <span
              key={i}
              className="demo-tour-target-label"
              style={{ left: r.x + 10, top: r.y + 9 }}
            >
              {r.label}
            </span>
          ),
      )}
      <div
        ref={card}
        className="demo-tour-card"
        role="dialog"
        aria-modal="false"
        aria-label="Canopy demo tour"
        aria-labelledby="demo-tour-title"
        aria-describedby="demo-tour-description"
        tabIndex={-1}
        style={{ left, top, width: cardWidth }}
        data-step={step}
      >
        <div className="demo-tour-heading">
          <span>
            {index + 1} OF {demoTourSteps.length} · {description.label}
          </span>
          <div
            className="demo-tour-dots"
            aria-label={`Step ${index + 1} of ${demoTourSteps.length}`}
          >
            {demoTourSteps.map((id, i) => (
              <i
                key={id}
                className={i === index ? "current" : i < index ? "past" : ""}
              />
            ))}
          </div>
          <button
            className="icon-button"
            aria-label="Close demo tour"
            onClick={onClose}
          >
            <X size={15} />
          </button>
        </div>
        <h3 id="demo-tour-title">{description.title}</h3>
        <p id="demo-tour-description" role="note">
          {description.body}
        </p>
        <div className="demo-tour-actions">
          <button className="tour-skip" onClick={onClose}>
            Skip tour
          </button>
          {index > 0 && (
            <button
              className="secondary-button"
              onClick={() => onStep(demoTourSteps[index - 1])}
            >
              <ChevronLeft size={13} />
              Back
            </button>
          )}
          <button className="primary-button" onClick={next}>
            {step === "repeat"
              ? "Run repeat"
              : index === demoTourSteps.length - 1
                ? "Finish tour"
                : "Next"}
            <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}
