import {
  detectCallsignService,
  isUsAmateurCallsign,
  normalizeAmateurCallsign,
  normalizeCallsign,
  normalizeGmrsCallsign,
  type CallsignServiceMode,
} from "./normalize";
import type {
  CallookResponse,
  CallsignRecord,
  HamdbResponse,
  SkywaveGmrsResponse,
} from "./types";

const APP_NAME = "sdrsense";
const HAMDB_URL = (call: string) =>
  `https://api.hamdb.org/v1/${encodeURIComponent(call)}/json/${APP_NAME}`;
const CALLOOK_URL = (call: string) =>
  `https://callook.info/${encodeURIComponent(call)}/json`;

function emptyRecord(callsign: string): CallsignRecord {
  return {
    callsign,
    status: "not_found",
    service: null,
    name: null,
    firstName: null,
    class: null,
    type: null,
    grid: null,
    latitude: null,
    longitude: null,
    addressLine1: null,
    addressLine2: null,
    country: null,
    expires: null,
    granted: null,
    trusteeCall: null,
    trusteeName: null,
    ulsUrl: null,
    frn: null,
    sources: [],
    error: null,
  };
}

function parseCoord(value: string | undefined): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function applyHamdb(record: CallsignRecord, data: HamdbResponse): void {
  const msg = data.hamdb?.messages?.status?.toUpperCase();
  const cs = data.hamdb?.callsign;
  if (msg === "NOT_FOUND" || !cs?.call || cs.call === "NOT_FOUND") return;

  record.sources.push("hamdb");
  record.service = "amateur";
  record.status = "valid";
  record.callsign = cs.call.toUpperCase();
  record.firstName = cs.fname?.trim() || null;
  record.name =
    [cs.fname, cs.mi, cs.name, cs.suffix]
      .map((p) => p?.trim())
      .filter(Boolean)
      .join(" ") ||
    cs.name?.trim() ||
    null;
  record.class = cs.class?.trim() || null;
  record.grid = cs.grid?.trim() || null;
  record.latitude = parseCoord(cs.lat);
  record.longitude = parseCoord(cs.lon);
  record.addressLine1 = cs.addr1?.trim() || null;
  const cityLine = [cs.addr2, cs.state, cs.zip]
    .map((p) => p?.trim())
    .filter(Boolean)
    .join(", ");
  record.addressLine2 = cityLine || null;
  record.country = cs.country?.trim() || null;
  record.expires = cs.expires?.trim() || null;

  const st = cs.status?.toUpperCase();
  if (st === "E" || st === "EXPIRED") record.status = "invalid";
  else if (st && st !== "A" && st !== "VALID" && st !== "") {
    if (!record.type) record.type = st;
  }
}

function applyCallook(record: CallsignRecord, data: CallookResponse): void {
  const status = data.status?.toUpperCase();
  if (status === "INVALID") {
    if (record.sources.length === 0) record.status = "not_found";
    return;
  }
  if (status !== "VALID") return;

  record.sources.push("callook");
  record.service = "amateur";
  record.status = "valid";
  if (data.current?.callsign) {
    record.callsign = data.current.callsign.toUpperCase();
  }
  if (data.name?.trim()) record.name = data.name.trim();
  if (data.current?.operClass?.trim()) {
    record.class = data.current.operClass.trim();
  }
  if (data.type?.trim()) record.type = data.type.trim();
  if (data.location?.gridsquare?.trim()) {
    record.grid = data.location.gridsquare.trim();
  }
  const lat = parseCoord(data.location?.latitude);
  const lon = parseCoord(data.location?.longitude);
  if (lat != null) record.latitude = lat;
  if (lon != null) record.longitude = lon;
  if (data.address?.line1?.trim()) {
    record.addressLine1 = data.address.line1.trim();
  }
  if (data.address?.line2?.trim()) {
    record.addressLine2 = data.address.line2.trim();
  }
  if (data.trustee?.callsign?.trim()) {
    record.trusteeCall = data.trustee.callsign.trim().toUpperCase();
  }
  if (data.trustee?.name?.trim()) {
    record.trusteeName = data.trustee.name.trim();
  }
  if (data.otherInfo?.expiryDate?.trim()) {
    record.expires = data.otherInfo.expiryDate.trim();
  }
  if (data.otherInfo?.grantDate?.trim()) {
    record.granted = data.otherInfo.grantDate.trim();
  }
  if (data.otherInfo?.ulsUrl?.trim()) {
    record.ulsUrl = data.otherInfo.ulsUrl.trim();
  }
  if (data.otherInfo?.frn?.trim()) {
    record.frn = data.otherInfo.frn.trim();
  }
  if (!record.country) record.country = "United States";
}

function applySkywave(record: CallsignRecord, data: SkywaveGmrsResponse): void {
  if (data.message?.toLowerCase().includes("not found") || !data.callsign) {
    return;
  }

  record.sources.push("skywave");
  record.service = "gmrs";
  record.callsign = data.callsign.toUpperCase();
  record.name = data.full_name?.trim() || data.name?.trim() || null;
  record.firstName = data.first_name?.trim() || null;
  record.type = "GMRS";
  record.class = data.is_active ? "Active" : "Inactive";
  record.country = "United States";
  record.addressLine1 = data.street?.trim() || null;
  const cityLine = [data.city, data.state, data.zip]
    .map((p) => (typeof p === "string" ? p.trim() : ""))
    .filter(Boolean)
    .join(", ");
  record.addressLine2 = cityLine || null;
  if (data.frn != null && data.frn !== "") {
    record.frn = String(data.frn);
  }
  if (typeof data.id === "number") {
    record.ulsUrl = `https://wireless2.fcc.gov/UlsApp/UlsSearch/license.jsp?licKey=${data.id}`;
  }

  const issued = data.statuses?.find((s) => s.value === "LIISS");
  const expired = data.statuses?.find((s) => s.value === "LIEXP");
  if (issued?.created_at) record.granted = issued.created_at;
  if (expired?.created_at) record.expires = expired.created_at;

  if (data.is_active === false || data.status === "E") {
    record.status = "invalid";
  } else {
    record.status = "valid";
  }
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`Lookup failed (${res.status})`);
  }
  return (await res.json()) as T;
}

/** Fetch JSON, allowing 404 bodies (Skywave not-found). */
async function fetchJsonAllow404<T extends { message?: string }>(
  url: string,
  signal?: AbortSignal,
): Promise<T> {
  const res = await fetch(url, {
    signal,
    headers: { Accept: "application/json" },
  });
  const data = (await res.json()) as T;
  if (!res.ok && res.status !== 404) {
    throw new Error(data.message ?? `Lookup failed (${res.status})`);
  }
  return data;
}

async function lookupAmateur(
  call: string,
  signal?: AbortSignal,
): Promise<CallsignRecord> {
  const record = emptyRecord(call);
  record.service = "amateur";

  const tasks: Promise<void>[] = [
    fetchJson<HamdbResponse>(HAMDB_URL(call), signal)
      .then((data) => applyHamdb(record, data))
      .catch(() => {
        /* try Callook alone */
      }),
  ];

  if (isUsAmateurCallsign(call)) {
    tasks.push(
      fetchJson<CallookResponse>(CALLOOK_URL(call), signal)
        .then((data) => applyCallook(record, data))
        .catch(() => {
          /* HamDB may still succeed */
        }),
    );
  }

  await Promise.all(tasks);

  if (record.sources.length === 0 && !isUsAmateurCallsign(call)) {
    try {
      const data = await fetchJson<CallookResponse>(CALLOOK_URL(call), signal);
      applyCallook(record, data);
    } catch {
      /* ignore */
    }
  }

  if (record.sources.length === 0 && !record.error) {
    record.status = "not_found";
    record.error = `No amateur record found for ${call}.`;
  }

  return record;
}

async function lookupGmrs(
  call: string,
  signal?: AbortSignal,
): Promise<CallsignRecord> {
  const record = emptyRecord(call);
  record.service = "gmrs";

  try {
    // Browser → local proxy (Skywave has no CORS). Node/smoke → direct.
    const url =
      typeof window === "undefined"
        ? `https://gmrslookup.skywave.org/${encodeURIComponent(call)}`
        : `/api/callsign/gmrs?call=${encodeURIComponent(call)}`;
    const data = await fetchJsonAllow404<SkywaveGmrsResponse>(url, signal);
    if (data.message?.toLowerCase().includes("not found")) {
      record.status = "not_found";
      record.error = `No GMRS record found for ${call}.`;
      return record;
    }
    applySkywave(record, data);
    if (record.sources.length === 0) {
      record.status = "not_found";
      record.error = `No GMRS record found for ${call}.`;
    }
  } catch (err) {
    record.status = "error";
    record.error =
      err instanceof Error ? err.message : "GMRS lookup failed.";
  }

  return record;
}

/**
 * Look up an amateur or GMRS callsign.
 * Amateur: HamDB + Callook (browser). GMRS: Skywave via `/api/callsign/gmrs`.
 */
export async function lookupCallsign(
  raw: string,
  signal?: AbortSignal,
  mode: CallsignServiceMode = "auto",
): Promise<CallsignRecord> {
  const call = normalizeCallsign(raw, mode);
  if (!call) {
    const hint =
      mode === "gmrs"
        ? "Enter a GMRS callsign (e.g. WRNT570)."
        : mode === "amateur"
          ? "Enter a valid amateur callsign (e.g. W1AW)."
          : "Enter an amateur (W1AW) or GMRS (WRNT570) callsign.";
    return {
      ...emptyRecord(raw.trim().toUpperCase() || "?"),
      status: "invalid",
      error: hint,
    };
  }

  const service =
    mode === "auto" ? detectCallsignService(call) : mode === "gmrs" ? "gmrs" : "amateur";

  if (service === "gmrs" || (mode === "gmrs" && normalizeGmrsCallsign(call))) {
    return lookupGmrs(call, signal);
  }

  const amateurCall = normalizeAmateurCallsign(call) ?? call;
  return lookupAmateur(amateurCall, signal);
}
