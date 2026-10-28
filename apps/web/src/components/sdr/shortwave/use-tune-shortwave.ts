"use client";

import {
  useRadioController,
  useSdrState,
} from "@/components/sdr/sdr-provider";

const SW_AM_BANDWIDTH_HZ = 10_000;

/** Tune AM to a shortwave broadcast frequency; start demo if idle. */
export function useTuneShortwave() {
  const controller = useRadioController();
  const radio = useSdrState();

  return (frequencyHz: number) => {
    const needsRf =
      radio.connection === "disconnected" ||
      radio.status === "idle" ||
      radio.status === "error";

    controller.setScheme("AM");
    controller.setBandwidth(SW_AM_BANDWIDTH_HZ);
    void controller.setFrequency(frequencyHz);
    controller.setOffset(0);
    if (needsRf) void controller.connectDemo();
  };
}
