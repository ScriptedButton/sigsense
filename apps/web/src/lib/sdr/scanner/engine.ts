import {
  createDefaultBanks,
  findBank,
  findChannel,
  newChannelId,
  scanableChannels,
} from "@/lib/sdr/scanner/banks";
import {
  loadScannerPersist,
  saveScannerPersist,
} from "@/lib/sdr/scanner/persist";
import type {
  ScanBank,
  ScanChannel,
  ScannerConfig,
  ScannerListener,
  ScannerRadioHooks,
  ScannerState,
} from "@/lib/sdr/scanner/types";
import { DEFAULT_SCANNER_CONFIG } from "@/lib/sdr/scanner/types";
import type { DemodScheme } from "@/lib/sdr/types";

const TICK_MS = 100;

/**
 * Programmable memory scanner — hops LO across a bank, holds on squelch
 * activity, persists banks/config to localStorage.
 */
export class ScannerEngine {
  private state: ScannerState;
  private listeners = new Set<ScannerListener>();
  private hooks: ScannerRadioHooks | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private dwellDeadline = 0;
  private quietSince = 0;
  private disposed = false;
  private hopping = false;

  constructor() {
    const persisted = loadScannerPersist();
    this.state = {
      status: "idle",
      config: persisted.config,
      banks: persisted.banks,
      scanIndex: 0,
      currentChannelId: null,
      holdReason: null,
      hopCount: 0,
      error: null,
    };
  }

  getState(): ScannerState {
    return this.state;
  }

  subscribe(listener: ScannerListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  setRadioHooks(hooks: ScannerRadioHooks): void {
    this.hooks = hooks;
  }

  patchConfig(partial: Partial<ScannerConfig>): void {
    const config = { ...this.state.config, ...partial };
    const bank = findBank(this.state.banks, config.activeBankId);
    if (!bank) {
      config.activeBankId =
        this.state.banks[0]?.id ?? DEFAULT_SCANNER_CONFIG.activeBankId;
    }
    this.patch({ config });
    this.persist();
  }

  setActiveBank(bankId: string): void {
    if (!findBank(this.state.banks, bankId)) return;
    const wasActive = this.state.status !== "idle";
    this.patchConfig({ activeBankId: bankId });
    if (wasActive) {
      void this.restartScan();
    }
  }

  async start(): Promise<void> {
    if (this.disposed) return;
    const bank = this.activeBank();
    const list = bank ? scanableChannels(bank) : [];
    if (!bank || list.length === 0) {
      this.patch({
        error: "No scanable channels in this bank — unlock or add some.",
      });
      return;
    }
    if (!this.hooks?.isConnected()) {
      this.patch({
        error: "Connect a radio or start Demo before scanning.",
      });
      return;
    }

    this.patch({
      status: "scanning",
      scanIndex: 0,
      hopCount: 0,
      holdReason: null,
      error:
        this.state.config.stopOnActivity && !this.hooks.getSquelchActive()
          ? "Enable squelch (or auto-squelch) so the scanner can stop on activity."
          : null,
    });
    this.startTicker();
    await this.tuneToScanIndex(0);
  }

  stop(): void {
    this.stopTicker();
    this.patch({
      status: "idle",
      holdReason: null,
      currentChannelId: null,
      error: null,
    });
  }

  pause(): void {
    if (this.state.status !== "scanning" && this.state.status !== "held") return;
    this.patch({ status: "paused", holdReason: "manual" });
  }

  resume(): void {
    if (this.state.status !== "paused" && this.state.status !== "held") return;
    this.patch({
      status: "scanning",
      holdReason: null,
      error: null,
    });
    this.dwellDeadline = performance.now() + this.state.config.dwellMs;
    this.quietSince = 0;
    this.startTicker();
  }

  /** Advance immediately to the next channel (from scan / hold / pause). */
  async skip(): Promise<void> {
    if (this.state.status === "idle") return;
    this.patch({
      status: "scanning",
      holdReason: null,
      error: null,
    });
    this.startTicker();
    await this.advance();
  }

  /** Manual hold on the current channel. */
  hold(): void {
    if (
      this.state.status !== "scanning" &&
      this.state.status !== "paused" &&
      this.state.status !== "held"
    ) {
      return;
    }
    this.patch({ status: "held", holdReason: "manual" });
  }

  toggleLockout(channelId: string): void {
    this.updateChannel(channelId, (c) => ({
      ...c,
      lockedOut: !c.lockedOut,
    }));
  }

  addChannel(input: {
    label: string;
    frequencyHz: number;
    scheme: DemodScheme;
    bandwidthHz?: number;
    bankId?: string;
  }): ScanChannel | null {
    const bankId = input.bankId ?? this.state.config.activeBankId;
    const bank = findBank(this.state.banks, bankId);
    if (!bank) return null;

    const channel: ScanChannel = {
      id: newChannelId(),
      label: input.label.trim() || "Channel",
      frequencyHz: Math.round(input.frequencyHz),
      scheme: input.scheme,
      bandwidthHz: input.bandwidthHz,
      lockedOut: false,
    };

    this.patchBanks(
      this.state.banks.map((b) =>
        b.id === bankId ? { ...b, channels: [...b.channels, channel] } : b,
      ),
    );
    return channel;
  }

  removeChannel(channelId: string): void {
    const bank = this.activeBank();
    if (!bank || bank.builtin) {
      // Builtin: only allow lockout, not delete — except custom extras? Keep simple: lockout only for builtin.
      // Actually allow remove only on non-builtin banks.
    }
    if (!bank || bank.builtin) return;

    this.patchBanks(
      this.state.banks.map((b) =>
        b.id === bank.id
          ? { ...b, channels: b.channels.filter((c) => c.id !== channelId) }
          : b,
      ),
    );
  }

  renameBank(bankId: string, name: string): void {
    const trimmed = name.trim();
    if (!trimmed) return;
    this.patchBanks(
      this.state.banks.map((b) =>
        b.id === bankId && !b.builtin ? { ...b, name: trimmed } : b,
      ),
    );
  }

  resetBuiltinBanks(): void {
    if (this.state.status !== "idle") this.stop();
    const custom = this.state.banks.find((b) => b.id === "custom");
    const defaults = createDefaultBanks();
    if (custom && custom.channels.length > 0) {
      const idx = defaults.findIndex((b) => b.id === "custom");
      if (idx >= 0) defaults[idx] = custom;
    }
    this.patch({
      banks: defaults,
      config: { ...this.state.config, activeBankId: defaults[0].id },
      scanIndex: 0,
      currentChannelId: null,
    });
    this.persist();
  }

  dispose(): void {
    this.disposed = true;
    this.stopTicker();
    this.listeners.clear();
  }

  // --- internals ----------------------------------------------------------

  private activeBank(): ScanBank | undefined {
    return findBank(this.state.banks, this.state.config.activeBankId);
  }

  private async restartScan(): Promise<void> {
    this.stop();
    await this.start();
  }

  private startTicker(): void {
    if (this.tickTimer) return;
    this.tickTimer = setInterval(() => this.onTick(), TICK_MS);
  }

  private stopTicker(): void {
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  }

  private onTick(): void {
    if (this.disposed || this.hopping) return;
    if (this.state.status === "paused" || this.state.status === "idle") return;

    const hooks = this.hooks;
    if (!hooks) return;

    const now = performance.now();
    const open = hooks.getSquelchOpen();
    const { config } = this.state;

    if (this.state.status === "held") {
      if (this.state.holdReason === "manual") return;
      if (!config.resumeOnQuiet || config.holdMs <= 0) return;

      if (open) {
        this.quietSince = 0;
        return;
      }
      if (this.quietSince === 0) {
        this.quietSince = now;
        return;
      }
      if (now - this.quietSince >= config.holdMs) {
        this.quietSince = 0;
        this.patch({ status: "scanning", holdReason: null });
        this.dwellDeadline = now + config.dwellMs;
        void this.advance();
      }
      return;
    }

    // scanning
    if (config.stopOnActivity && hooks.getSquelchActive() && open) {
      this.quietSince = 0;
      this.patch({ status: "held", holdReason: "activity" });
      return;
    }

    if (now >= this.dwellDeadline) {
      void this.advance();
    }
  }

  private async advance(): Promise<void> {
    const bank = this.activeBank();
    if (!bank) return;
    const list = scanableChannels(bank);
    if (list.length === 0) {
      this.stop();
      this.patch({ error: "No scanable channels left in this bank." });
      return;
    }
    const next = (this.state.scanIndex + 1) % list.length;
    await this.tuneToScanIndex(next);
  }

  private async tuneToScanIndex(index: number): Promise<void> {
    const bank = this.activeBank();
    const hooks = this.hooks;
    if (!bank || !hooks) return;
    const list = scanableChannels(bank);
    if (list.length === 0) return;

    const safeIndex = ((index % list.length) + list.length) % list.length;
    const channel = list[safeIndex];
    this.hopping = true;
    try {
      hooks.setScheme(channel.scheme);
      if (channel.bandwidthHz != null) {
        hooks.setBandwidth(channel.bandwidthHz);
      }
      hooks.setOffset(0);
      await hooks.setFrequency(channel.frequencyHz);
      this.dwellDeadline = performance.now() + this.state.config.dwellMs;
      this.quietSince = 0;
      this.patch({
        scanIndex: safeIndex,
        currentChannelId: channel.id,
        hopCount: this.state.hopCount + 1,
      });
    } catch (err) {
      this.patch({
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      this.hopping = false;
    }
  }

  private updateChannel(
    channelId: string,
    map: (c: ScanChannel) => ScanChannel,
  ): void {
    this.patchBanks(
      this.state.banks.map((b) => ({
        ...b,
        channels: b.channels.map((c) => (c.id === channelId ? map(c) : c)),
      })),
    );
  }

  private patchBanks(banks: ScanBank[]): void {
    // If current channel vanished from scanable set, clamp index.
    const bank = findBank(banks, this.state.config.activeBankId);
    const list = bank ? scanableChannels(bank) : [];
    let scanIndex = this.state.scanIndex;
    let currentChannelId = this.state.currentChannelId;
    if (currentChannelId && bank && !findChannel(bank, currentChannelId)) {
      currentChannelId = null;
    }
    if (list.length === 0) {
      scanIndex = 0;
    } else if (scanIndex >= list.length) {
      scanIndex = 0;
    }
    this.patch({ banks, scanIndex, currentChannelId });
    this.persist();
  }

  private persist(): void {
    saveScannerPersist(this.state.config, this.state.banks);
  }

  private patch(partial: Partial<ScannerState>): void {
    this.state = { ...this.state, ...partial };
    for (const listener of this.listeners) {
      listener(this.state);
    }
  }
}
