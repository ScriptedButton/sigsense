export type CallsignStatus =
  | "valid"
  | "invalid"
  | "not_found"
  | "error";

export type CallsignService = "amateur" | "gmrs";

export type CallsignRecord = {
  callsign: string;
  status: CallsignStatus;
  service: CallsignService | null;
  /** Licensee / club name. */
  name: string | null;
  firstName: string | null;
  class: string | null;
  type: string | null;
  grid: string | null;
  latitude: number | null;
  longitude: number | null;
  addressLine1: string | null;
  addressLine2: string | null;
  country: string | null;
  expires: string | null;
  granted: string | null;
  trusteeCall: string | null;
  trusteeName: string | null;
  ulsUrl: string | null;
  frn: string | null;
  /** Which directories contributed data. */
  sources: Array<"hamdb" | "callook" | "skywave">;
  error: string | null;
};

export type CallookResponse = {
  status: string;
  type?: string;
  name?: string;
  current?: { callsign?: string; operClass?: string };
  previous?: { callsign?: string; operClass?: string };
  trustee?: { callsign?: string; name?: string };
  address?: { line1?: string; line2?: string; attn?: string };
  location?: {
    latitude?: string;
    longitude?: string;
    gridsquare?: string;
  };
  otherInfo?: {
    grantDate?: string;
    expiryDate?: string;
    lastActionDate?: string;
    frn?: string;
    ulsUrl?: string;
  };
};

export type HamdbResponse = {
  hamdb?: {
    callsign?: {
      call?: string;
      class?: string;
      expires?: string;
      status?: string;
      grid?: string;
      lat?: string;
      lon?: string;
      fname?: string;
      mi?: string;
      name?: string;
      suffix?: string;
      addr1?: string;
      addr2?: string;
      state?: string;
      zip?: string;
      country?: string;
    };
    messages?: { status?: string };
  };
};

/** Skywave Communications GMRS lookup JSON. */
export type SkywaveGmrsResponse = {
  id?: number;
  type?: string;
  entity_type?: string;
  callsign?: string;
  name?: string;
  status?: string;
  service?: string;
  first_name?: string | null;
  initial?: string | null;
  last_name?: string | null;
  suffix?: string | null;
  street?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  other_name?: string | null;
  frn?: number | string | null;
  full_name?: string | null;
  is_active?: boolean;
  message?: string;
  statuses?: Array<{ value?: string; created_at?: string }>;
};
