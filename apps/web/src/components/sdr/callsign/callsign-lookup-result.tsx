"use client";

import type { CallsignRecord } from "@/lib/sdr/callsign/types";

type CallsignLookupResultProps = {
  record: CallsignRecord | null;
  busy: boolean;
};

function Row({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="callsign-result-row">
      <dt>{label}</dt>
      <dd className="whitespace-pre-line">{value}</dd>
    </div>
  );
}

function formatCoords(lat: number | null, lon: number | null): string | null {
  if (lat == null || lon == null) return null;
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(4)}°${ns} · ${Math.abs(lon).toFixed(4)}°${ew}`;
}

function sourceLabel(source: CallsignRecord["sources"][number]): string {
  switch (source) {
    case "hamdb":
      return "HamDB";
    case "callook":
      return "Callook";
    case "skywave":
      return "Skywave GMRS";
  }
}

export function CallsignLookupResult({
  record,
  busy,
}: CallsignLookupResultProps) {
  if (busy && !record) {
    return (
      <p className="mt-3 text-sm text-muted" aria-live="polite">
        Looking up callsign…
      </p>
    );
  }

  if (!record) {
    return (
      <p className="mt-3 text-sm text-muted">
        Enter an amateur or GMRS callsign, or click one in Morse / FT8 decode.
      </p>
    );
  }

  if (record.status === "invalid" || record.status === "not_found") {
    return (
      <p className="mt-3 text-sm text-warn" role="status">
        {record.error ?? `No record for ${record.callsign}.`}
      </p>
    );
  }

  if (record.status === "error") {
    return (
      <p className="mt-3 text-sm text-danger" role="alert">
        {record.error ?? "Lookup failed."}
      </p>
    );
  }

  const mapsUrl =
    record.latitude != null && record.longitude != null
      ? `https://www.openstreetmap.org/?mlat=${record.latitude}&mlon=${record.longitude}#map=10/${record.latitude}/${record.longitude}`
      : null;

  const qrzUrl =
    record.service === "amateur"
      ? `https://www.qrz.com/db/${encodeURIComponent(record.callsign)}`
      : null;

  const address = [record.addressLine1, record.addressLine2]
    .filter(Boolean)
    .join("\n");

  const serviceBadge =
    record.service === "gmrs"
      ? "GMRS"
      : record.service === "amateur"
        ? "Amateur"
        : null;

  return (
    <div className="callsign-result mt-3" aria-live="polite">
      <div className="callsign-result-head">
        <p className="callsign-result-call">{record.callsign}</p>
        <div className="callsign-result-badges">
          {serviceBadge ? (
            <span className="callsign-badge">{serviceBadge}</span>
          ) : null}
          {record.class ? (
            <span className="callsign-badge callsign-badge-muted">
              {record.class}
            </span>
          ) : null}
          {record.type && record.type !== "GMRS" ? (
            <span className="callsign-badge callsign-badge-muted">
              {record.type}
            </span>
          ) : null}
        </div>
      </div>

      <dl className="callsign-result-dl">
        <Row label="Name" value={record.name} />
        <Row label="Grid" value={record.grid} />
        <Row
          label="Location"
          value={formatCoords(record.latitude, record.longitude)}
        />
        <Row label="Address" value={address || null} />
        <Row label="Country" value={record.country} />
        <Row label="FRN" value={record.frn} />
        <Row label="Granted" value={record.granted} />
        <Row label="Expires" value={record.expires} />
        <Row
          label="Trustee"
          value={
            record.trusteeCall
              ? [record.trusteeCall, record.trusteeName]
                  .filter(Boolean)
                  .join(" · ")
              : null
          }
        />
      </dl>

      <div className="callsign-result-links">
        {qrzUrl ? (
          <a
            className="btn-secondary callsign-link-btn"
            href={qrzUrl}
            target="_blank"
            rel="noreferrer"
          >
            QRZ
          </a>
        ) : null}
        {record.ulsUrl ? (
          <a
            className="btn-secondary callsign-link-btn"
            href={record.ulsUrl}
            target="_blank"
            rel="noreferrer"
          >
            FCC ULS
          </a>
        ) : null}
        {mapsUrl ? (
          <a
            className="btn-secondary callsign-link-btn"
            href={mapsUrl}
            target="_blank"
            rel="noreferrer"
          >
            Map
          </a>
        ) : null}
      </div>

      <p className="mt-2 text-[0.65rem] text-muted">
        Source{record.sources.length === 1 ? "" : "s"}:{" "}
        {record.sources.map(sourceLabel).join(" · ")}
        {record.sources.includes("skywave") ? (
          <>
            {" "}
            (
            <a
              className="underline decoration-line underline-offset-2 hover:text-ink"
              href="https://gmrslookup.skywave.org"
              target="_blank"
              rel="noreferrer"
            >
              Skywave Communications
            </a>
            )
          </>
        ) : null}
      </p>
    </div>
  );
}
