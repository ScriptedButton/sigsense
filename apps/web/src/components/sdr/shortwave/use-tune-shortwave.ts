"use client";

import {
  useRadioController,
  useSdrState,
} from "@/components/sdr/sdr-provider";

const SW_AM_BANDWIDTH_HZ = 6_000;

/** Tune a broadcast on the selected receiver; disconnected tuning sets the dial. */
export function useTuneShortwave() {
  const controller = useRadioController();
  const radio = useSdrState();

  return (frequencyHz: number) => {
    if (radio.scheme !== "AM") {
      controller.setScheme("AM");
      controller.setBandwidth(SW_AM_BANDWIDTH_HZ);
    }
    controller.setAutoSquelch(false);
    controller.setSquelch(0);
    controller.tuneToHz(frequencyHz);
  };
}
