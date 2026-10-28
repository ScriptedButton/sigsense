"use client";

import { useEffect, useState } from "react";
import {
  DEFAULT_LISTENER,
} from "@/lib/sdr/wefax/reachability";

export type ListenerLocation = {
  lat: number;
  lon: number;
  /** True when using browser geolocation. */
  precise: boolean;
};

export function useListenerLocation(): {
  location: ListenerLocation;
  requestPrecise: () => void;
  locating: boolean;
} {
  const [location, setLocation] = useState<ListenerLocation>({
    ...DEFAULT_LISTENER,
    precise: false,
  });
  const [locating, setLocating] = useState(false);

  const requestPrecise = () => {
    if (!navigator.geolocation) return;
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          precise: true,
        });
        setLocating(false);
      },
      () => setLocating(false),
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 600_000 },
    );
  };

  useEffect(() => {
    // Soft prompt once — fail silently to US centroid.
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          precise: true,
        });
      },
      () => {},
      { enableHighAccuracy: false, timeout: 8_000, maximumAge: 600_000 },
    );
  }, []);

  return { location, requestPrecise, locating };
}
