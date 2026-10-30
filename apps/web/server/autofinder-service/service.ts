import { ReceiverBrokerClient } from "../receiver-mcp/tools";
import type { FinderState } from "../../src/lib/sdr/autofinder/engine";
import type { SdrState } from "../../src/lib/sdr/types";
import type { ServiceAction, ServiceStatus } from "../../src/lib/sdr/autofinder/service-types";
export type { ServiceStatus } from "../../src/lib/sdr/autofinder/service-types";

type Session = { id: string; url: string; state: SdrState };
type Broker = Pick<ReceiverBrokerClient, "sessions" | "command">;
const ACTIVE = new Set<FinderState["status"]>(["starting", "searching", "checking", "listening", "keeping"]);

/** Supervises only the dedicated receiverAgent browser and yields the RTL device to interactive browsers. */
export class AutofinderService {
  private owned = false;
  private selected: string | null = null;
  private reservedUntil = 0;
  private retryFinderAt = 0;
  private startedFinder = false;
  private queue: Promise<unknown> = Promise.resolve();
  public enabled = true;

  constructor(
    private broker: Broker = new ReceiverBrokerClient(),
    private rtlUrl = process.env.SIGSENSE_AUTOFINDER_RTL_URL ?? "http://127.0.0.1:8081",
    private publish: (status: ServiceStatus) => Promise<void> = async () => {},
    private now: () => number = Date.now,
  ) {}

  private exclusive<T>(work: () => Promise<T>): Promise<T> {
    const next = this.queue.then(work);
    this.queue = next.catch(() => {});
    return next;
  }

  private status(status: ServiceStatus["status"], message: string, receiver: SdrState | null = null, finder: FinderState | null = null) {
    return this.publish({ at: new Date().toISOString(), status, message, sessionId: this.selected, receiver, finder });
  }

  private command(action: Parameters<ReceiverBrokerClient["command"]>[0], args: Record<string, unknown> = {}) {
    if (!this.selected) throw new Error("No dedicated receiver session");
    return this.broker.command(action, args, this.selected, 30_000);
  }

  tick(): Promise<ServiceStatus["status"]> { return this.exclusive(() => this.scan()); }

  private async scan(): Promise<ServiceStatus["status"]> {
    const sessions = await this.broker.sessions() as Session[];
    const dedicated = sessions.find((session) => {
      try { return new URL(session.url).searchParams.get("receiverAgent") === "1"; } catch { return false; }
    });
    if (!dedicated) {
      this.selected = null;
      this.owned = false;
      this.startedFinder = false;
      await this.status(this.enabled ? "waiting" : "paused", this.enabled ? "Waiting for the dedicated receiver browser." : "Background search paused.");
      return this.enabled ? "waiting" : "paused";
    }
    if (this.selected !== dedicated.id) { this.owned = false; this.startedFinder = false; }
    this.selected = dedicated.id;
    if (dedicated.state?.connection === "rtltcp" && dedicated.state.status === "playing") this.owned = true;
    const competing = sessions.find((session) => session.id !== dedicated.id && session.state?.connection === "rtltcp" && ["playing", "starting"].includes(session.state.status));
    if (!this.enabled || competing || this.now() < this.reservedUntil) {
      await this.release();
      const paused = !this.enabled;
      await this.status(paused ? "paused" : "yielding", paused ? "Background search paused. Saved findings remain available." : "Receiver is available for live listening; search resumes when it is free.");
      return paused ? "paused" : "yielding";
    }

    let receiver = await this.command("get_state") as SdrState;
    let connectedNow = false;
    if (receiver.status !== "playing" || receiver.connection !== "rtltcp") {
      if (receiver.status === "playing") await this.command("stop");
      await this.status("starting", "Connecting the dedicated receiver to the real RTL-SDR stream.", receiver);
      // Take responsibility before connecting so a failed start still gets cleaned up on shutdown.
      this.owned = true;
      receiver = await this.command("connect", { type: "rtltcp", url: this.rtlUrl }) as SdrState;
      if (receiver.status !== "playing" || receiver.connection !== "rtltcp") throw new Error("Dedicated receiver did not enter real rtl_tcp playback");
      connectedNow = true;
      this.startedFinder = false;
    }

    let finder = await this.command("autofinder_state") as FinderState;
    // Save the previous run's checks before start resets its recent list.
    // A previous demo/other connection's recent list cannot establish a real received signal.
    const realRun = !connectedNow && (this.startedFinder || ACTIVE.has(finder.status));
    if (realRun) await this.status("scanning", finder.message, receiver, finder);
    if (finder.status === "error" && !connectedNow) {
      if (!this.retryFinderAt) this.retryFinderAt = this.now() + 10_000;
      if (this.now() < this.retryFinderAt) {
        await this.status("error", `${finder.message} Retrying shortly…`, receiver, realRun ? finder : null);
        return "error";
      }
    }
    if (!ACTIVE.has(finder.status)) {
      if (finder.status === "stopping") return "starting";
      await this.status("starting", finder.status === "error" ? `Restarting after: ${finder.message}` : "Starting continuous shortwave signal search.", receiver, realRun ? finder : null);
      finder = await this.command("autofinder_start", { maxListenMs: 30_000 }) as FinderState;
      this.startedFinder = true;
      this.retryFinderAt = 0;
    }
    this.owned = true;
    await this.status("scanning", finder.message, receiver, finder);
    return "scanning";
  }

  private async release(): Promise<void> {
    if (this.selected && this.owned) {
      const finder = await this.command("autofinder_state") as FinderState;
      await this.status("yielding", "Releasing receiver for live listening…", null, this.startedFinder || ACTIVE.has(finder.status) ? finder : null);
      await this.command("autofinder_stop");
      await this.command("stop");
    }
    this.owned = false;
    this.startedFinder = false;
  }

  control(action: ServiceAction): Promise<void> {
    return this.exclusive(async () => {
      if (action === "skip") {
        if (!this.selected || !this.owned) throw new Error("No signal is being checked by the background finder.");
        await this.command("autofinder_skip");
      } else if (action === "resume") {
        this.enabled = true;
        this.reservedUntil = 0;
      } else {
        if (action === "pause") this.enabled = false;
        else this.reservedUntil = this.now() + 30_000;
      }
      if (action === "pause" || action === "resume") await this.status(action === "pause" ? "paused" : "starting", action === "pause" ? "Pausing background search…" : "Resuming background search…");
      await this.scan();
    });
  }

  stop(): Promise<void> {
    return this.exclusive(async () => {
      await this.release();
      await this.status("stopped", "Service stopped and released its receiver.");
    });
  }
}
