/**
 * USCG / NOAA HF radiofax stations.
 *
 * Assigned frequencies are official F3C centers. USB dial frequency is
 * assigned − 1.9 kHz (carrier). Schedules sourced from NWS tgftp fax/*.txt
 * (Boston, New Orleans, Pt. Reyes, Kodiak, Honolulu).
 */

/** Minutes from 00:00 UTC (0…1439). */
export type UtcMinute = number;

export type WefaxFreqWindow = {
  /** Inclusive start, UTC minutes. */
  startMin: UtcMinute;
  /** Exclusive end, UTC minutes. May be < startMin for overnight. */
  endMin: UtcMinute;
};

export type WefaxFrequency = {
  id: string;
  /** Assigned (center) frequency in Hz. */
  assignedHz: number;
  /** When this transmitter is scheduled (omit = all broadcast times). */
  windows?: readonly WefaxFreqWindow[];
};

export type WefaxProduct = {
  /** Start time UTC minutes. */
  startMin: UtcMinute;
  title: string;
};

export type WefaxStation = {
  id: string;
  callsign: string;
  name: string;
  region: string;
  lat: number;
  lon: number;
  powerKw: number;
  frequencies: readonly WefaxFrequency[];
  /** Overall on-air windows for the station. */
  windows: readonly WefaxFreqWindow[];
  /** Chart start times (UTC). Typical chart ~10–12 min at 120 LPM. */
  products: readonly WefaxProduct[];
  sourceUrl: string;
};

/** Carrier / USB dial offset from assigned frequency (NOAA note). */
export const WEFAX_CARRIER_OFFSET_HZ = 1_900;

/** Typical chart duration used for “on air now” detection. */
export const WEFAX_CHART_DURATION_MIN = 12;

export function wefaxTuneHz(assignedHz: number): number {
  return assignedHz - WEFAX_CARRIER_OFFSET_HZ;
}

export const WEFAX_STATIONS: readonly WefaxStation[] = [
  {
    id: "nmf",
    callsign: "NMF",
    name: "Boston",
    region: "NW Atlantic",
    lat: 42.24,
    lon: -70.95,
    powerKw: 4,
    sourceUrl: "https://tgftp.nws.noaa.gov/fax/hfmarsh.txt",
    frequencies: [
      {
        id: "nmf-4235",
        assignedHz: 4_235_000,
        windows: [{ startMin: 150, endMin: 639 }],
      },
      { id: "nmf-6340", assignedHz: 6_340_500 },
      { id: "nmf-9110", assignedHz: 9_110_000 },
      {
        id: "nmf-12750",
        assignedHz: 12_750_000,
        windows: [{ startMin: 840, endMin: 1359 }],
      },
    ],
    windows: [
      { startMin: 150, endMin: 639 },
      { startMin: 840, endMin: 1359 },
    ],
    products: [
      { startMin: 150, title: "Test pattern" },
      { startMin: 153, title: "Preliminary surface analysis" },
      { startMin: 163, title: "Broadcast schedule (1)" },
      { startMin: 174, title: "Broadcast schedule (2)" },
      { startMin: 185, title: "Request for comments" },
      { startMin: 195, title: "Wind/wave analysis" },
      { startMin: 205, title: "Surface analysis · NE Atlantic" },
      { startMin: 218, title: "Surface analysis · NW Atlantic" },
      { startMin: 231, title: "Satellite image" },
      { startMin: 242, title: "500 mb analysis" },
      { startMin: 258, title: "Ice chart" },
      { startMin: 272, title: "Cyclone / high wind-waves" },
      { startMin: 465, title: "Test pattern" },
      { startMin: 475, title: "Preliminary surface analysis" },
      { startMin: 485, title: "24h surface forecast" },
      { startMin: 495, title: "24h wind/wave forecast" },
      { startMin: 505, title: "24h 500 mb forecast" },
      { startMin: 515, title: "36h 500 mb forecast" },
      { startMin: 525, title: "48h surface forecast" },
      { startMin: 535, title: "48h wind/wave forecast" },
      { startMin: 545, title: "48h 500 mb forecast" },
      { startMin: 555, title: "48h wave period" },
      { startMin: 565, title: "Surface analysis · NE Atlantic" },
      { startMin: 578, title: "Surface analysis · NW Atlantic" },
      { startMin: 591, title: "Satellite image" },
      { startMin: 602, title: "Rebroadcast · NE Atlantic" },
      { startMin: 615, title: "Rebroadcast · NW Atlantic" },
      { startMin: 628, title: "Cyclone / high wind-waves" },
      { startMin: 639, title: "Ice service chart" },
      { startMin: 840, title: "Test pattern" },
      { startMin: 845, title: "Broadcast schedule (1)" },
      { startMin: 860, title: "Broadcast schedule (2)" },
      { startMin: 873, title: "Product notice" },
      { startMin: 883, title: "Preliminary surface analysis" },
      { startMin: 893, title: "Satellite image" },
      { startMin: 915, title: "Wind/wave analysis" },
      { startMin: 925, title: "Surface analysis · NE Atlantic" },
      { startMin: 938, title: "Surface analysis · NW Atlantic" },
      { startMin: 1040, title: "Test pattern" },
      { startMin: 1043, title: "Rebroadcast · NE Atlantic" },
      { startMin: 1056, title: "Rebroadcast · NW Atlantic" },
      { startMin: 1069, title: "500 mb analysis" },
      { startMin: 1079, title: "Sea state analysis" },
      { startMin: 1090, title: "24h surface forecast" },
      { startMin: 1104, title: "Cyclone / high wind-waves" },
      { startMin: 1115, title: "24h wind/wave forecast" },
      { startMin: 1135, title: "24h 500 mb forecast" },
      { startMin: 1145, title: "36h 500 mb forecast" },
      { startMin: 1155, title: "96h surface forecast" },
      { startMin: 1165, title: "96h wind/wave forecast" },
      { startMin: 1175, title: "96h 500 mb forecast" },
      { startMin: 1185, title: "96h wave period" },
      { startMin: 1195, title: "48h surface forecast" },
      { startMin: 1205, title: "48h wind/wave forecast" },
      { startMin: 1215, title: "48h 500 mb forecast" },
      { startMin: 1225, title: "48h wave period" },
      { startMin: 1235, title: "Preliminary surface analysis" },
      { startMin: 1245, title: "72h surface forecast" },
      { startMin: 1255, title: "72h wind/wave forecast" },
      { startMin: 1265, title: "72h 500 mb forecast" },
      { startMin: 1275, title: "72h wave period" },
      { startMin: 1285, title: "Surface analysis · NE Atlantic" },
      { startMin: 1298, title: "Surface analysis · NW Atlantic" },
      { startMin: 1311, title: "Satellite image" },
      { startMin: 1322, title: "Rebroadcast · NE Atlantic" },
      { startMin: 1335, title: "Rebroadcast · NW Atlantic" },
      { startMin: 1348, title: "Cyclone / high wind-waves" },
      { startMin: 1359, title: "Ice service chart" },
    ],
  },
  {
    id: "nmg",
    callsign: "NMG",
    name: "New Orleans",
    region: "Gulf / Caribbean / Trop Atlantic",
    lat: 29.95,
    lon: -90.07,
    powerKw: 4,
    sourceUrl: "https://tgftp.nws.noaa.gov/fax/hfgulf.txt",
    frequencies: [
      { id: "nmg-4318", assignedHz: 4_317_900 },
      { id: "nmg-8504", assignedHz: 8_503_900 },
      { id: "nmg-12790", assignedHz: 12_789_900 },
      {
        id: "nmg-17146",
        assignedHz: 17_146_400,
        windows: [{ startMin: 720, endMin: 1245 }],
      },
    ],
    windows: [
      { startMin: 0, endMin: 525 },
      { startMin: 720, endMin: 1245 },
    ],
    products: [
      { startMin: 0, title: "Test pattern" },
      { startMin: 5, title: "Tropical surface analysis · W" },
      { startMin: 20, title: "Tropical surface analysis · E" },
      { startMin: 35, title: "24h wind/wave rebroadcast" },
      { startMin: 45, title: "48h wind/wave rebroadcast" },
      { startMin: 55, title: "72h wind/wave rebroadcast" },
      { startMin: 65, title: "24h surface rebroadcast" },
      { startMin: 75, title: "48h surface rebroadcast" },
      { startMin: 85, title: "72h surface rebroadcast" },
      { startMin: 95, title: "Cyclone / high wind-waves" },
      { startMin: 120, title: "GOES IR tropical satellite" },
      { startMin: 135, title: "Sea state analysis" },
      { startMin: 165, title: "High seas forecast" },
      { startMin: 360, title: "Test pattern" },
      { startMin: 365, title: "Tropical surface analysis · W" },
      { startMin: 380, title: "Tropical surface analysis · E" },
      { startMin: 395, title: "48h wave period/swell" },
      { startMin: 415, title: "24h surface forecast" },
      { startMin: 425, title: "48h surface forecast" },
      { startMin: 435, title: "72h surface forecast" },
      { startMin: 445, title: "24h wind/wave forecast" },
      { startMin: 455, title: "Cyclone / high wind-waves" },
      { startMin: 470, title: "48h wind/wave forecast" },
      { startMin: 480, title: "GOES IR tropical satellite" },
      { startMin: 495, title: "72h wind/wave forecast" },
      { startMin: 505, title: "72h wave period/swell" },
      { startMin: 525, title: "High seas forecast" },
      { startMin: 720, title: "Test pattern" },
      { startMin: 725, title: "Tropical surface analysis · W" },
      { startMin: 740, title: "Tropical surface analysis · E" },
      { startMin: 755, title: "24h wind/wave rebroadcast" },
      { startMin: 765, title: "48h wind/wave rebroadcast" },
      { startMin: 775, title: "72h wind/wave rebroadcast" },
      { startMin: 785, title: "24h surface rebroadcast" },
      { startMin: 795, title: "48h surface rebroadcast" },
      { startMin: 805, title: "72h surface rebroadcast" },
      { startMin: 815, title: "Cyclone / high wind-waves" },
      { startMin: 830, title: "GOES IR tropical satellite" },
      { startMin: 845, title: "Sea state analysis" },
      { startMin: 865, title: "High seas forecast" },
      { startMin: 1080, title: "Test pattern" },
      { startMin: 1085, title: "Tropical surface analysis · W" },
      { startMin: 1100, title: "Tropical surface analysis · E" },
      { startMin: 1115, title: "48h wave period/swell" },
      { startMin: 1135, title: "24h surface forecast" },
      { startMin: 1145, title: "48h surface forecast" },
      { startMin: 1155, title: "72h surface forecast" },
      { startMin: 1165, title: "24h wind/wave forecast" },
      { startMin: 1175, title: "Cyclone / high wind-waves" },
      { startMin: 1190, title: "48h wind/wave forecast" },
      { startMin: 1200, title: "GOES IR tropical satellite" },
      { startMin: 1215, title: "72h wind/wave forecast" },
      { startMin: 1225, title: "Broadcast schedule" },
      { startMin: 1245, title: "High seas forecast" },
    ],
  },
  {
    id: "nmc",
    callsign: "NMC",
    name: "Pt. Reyes",
    region: "NE / SE Pacific",
    lat: 38.1,
    lon: -122.93,
    powerKw: 4,
    sourceUrl: "https://tgftp.nws.noaa.gov/fax/hfreyes.txt",
    frequencies: [
      {
        id: "nmc-4346",
        assignedHz: 4_346_000,
        windows: [{ startMin: 100, endMin: 968 }],
      },
      { id: "nmc-8682", assignedHz: 8_682_000 },
      { id: "nmc-12786", assignedHz: 12_786_000 },
      { id: "nmc-17151", assignedHz: 17_151_200 },
      {
        id: "nmc-22527",
        assignedHz: 22_527_000,
        windows: [{ startMin: 1120, endMin: 1436 }],
      },
    ],
    windows: [
      { startMin: 100, endMin: 968 },
      { startMin: 1100, endMin: 1436 },
    ],
    products: [
      { startMin: 100, title: "Test pattern" },
      { startMin: 103, title: "NE Pacific GOES IR" },
      { startMin: 114, title: "Pacific GOES IR" },
      { startMin: 125, title: "Tropical sea state" },
      { startMin: 135, title: "Tropical 48h surface" },
      { startMin: 145, title: "Tropical 48h wind/wave" },
      { startMin: 155, title: "Tropical 72h wind/wave" },
      { startMin: 165, title: "500 mb analysis" },
      { startMin: 175, title: "Sea state / wind-wave" },
      { startMin: 185, title: "Prelim surface · NE Pac" },
      { startMin: 198, title: "Prelim surface · NW Pac" },
      { startMin: 211, title: "Final surface · NE Pac" },
      { startMin: 224, title: "Final surface · NW Pac" },
      { startMin: 237, title: "Cyclone / high wind-waves" },
      { startMin: 248, title: "Tropical surface analysis" },
      { startMin: 415, title: "Test pattern" },
      { startMin: 417, title: "96h 500 mb rebroadcast" },
      { startMin: 437, title: "Tropical GOES IR" },
      { startMin: 448, title: "Wind/wave analysis" },
      { startMin: 458, title: "24h surface forecast" },
      { startMin: 468, title: "24h wind/wave forecast" },
      { startMin: 478, title: "24h 500 mb forecast" },
      { startMin: 508, title: "48h surface forecast" },
      { startMin: 518, title: "48h wind/wave forecast" },
      { startMin: 528, title: "48h 500 mb forecast" },
      { startMin: 538, title: "48h wave period" },
      { startMin: 548, title: "Pacific GOES IR" },
      { startMin: 559, title: "Surface analysis · NE Pac" },
      { startMin: 572, title: "Surface analysis · NW Pac" },
      { startMin: 585, title: "Tropical surface analysis" },
      { startMin: 599, title: "Tropical 24h wind/wave" },
      { startMin: 609, title: "Cyclone / high wind-waves" },
      { startMin: 680, title: "Test pattern" },
      { startMin: 684, title: "Broadcast schedule (1)" },
      { startMin: 695, title: "Broadcast schedule (2)" },
      { startMin: 706, title: "Request for comments" },
      { startMin: 717, title: "Product notice" },
      { startMin: 728, title: "Tropical 48h wind/wave" },
      { startMin: 738, title: "Tropical 72h wind/wave" },
      { startMin: 748, title: "Tropical 48h wave period" },
      { startMin: 840, title: "Test pattern" },
      { startMin: 843, title: "NE Pacific GOES IR" },
      { startMin: 854, title: "Pacific GOES IR" },
      { startMin: 865, title: "Tropical sea state" },
      { startMin: 875, title: "Tropical 48h surface" },
      { startMin: 885, title: "500 mb analysis" },
      { startMin: 895, title: "Sea state / wind-wave" },
      { startMin: 905, title: "Prelim surface · NE Pac" },
      { startMin: 918, title: "Prelim surface · NW Pac" },
      { startMin: 931, title: "Final surface · NE Pac" },
      { startMin: 944, title: "Final surface · NW Pac" },
      { startMin: 957, title: "Cyclone / high wind-waves" },
      { startMin: 968, title: "Tropical surface analysis" },
      { startMin: 1100, title: "24h surface forecast" },
      { startMin: 1110, title: "24h wind/wave forecast" },
      { startMin: 1120, title: "24h 500 mb forecast" },
      { startMin: 1130, title: "SST analysis" },
      { startMin: 1140, title: "SST analysis" },
      { startMin: 1153, title: "Tropical GOES IR" },
      { startMin: 1163, title: "Wind/wave analysis" },
      { startMin: 1173, title: "96h surface forecast" },
      { startMin: 1183, title: "96h wind/wave forecast" },
      { startMin: 1193, title: "96h 500 mb forecast" },
      { startMin: 1203, title: "96h wave period" },
      { startMin: 1213, title: "48h surface forecast" },
      { startMin: 1223, title: "48h wind/wave forecast" },
      { startMin: 1233, title: "48h 500 mb forecast" },
      { startMin: 1243, title: "48h wave period" },
      { startMin: 1253, title: "72h surface forecast" },
      { startMin: 1263, title: "72h wind/wave forecast" },
      { startMin: 1273, title: "Pacific GOES IR" },
      { startMin: 1284, title: "Surface analysis · NE Pac" },
      { startMin: 1297, title: "Surface analysis · NW Pac" },
      { startMin: 1310, title: "Tropical surface analysis" },
      { startMin: 1324, title: "Tropical 24h wind/wave" },
      { startMin: 1400, title: "Test pattern" },
      { startMin: 1404, title: "Broadcast schedule (1)" },
      { startMin: 1415, title: "Broadcast schedule (2)" },
      { startMin: 1428, title: "Tropical 48h wave period" },
      { startMin: 1436, title: "Tropical 72h wave period" },
    ],
  },
  {
    id: "noj",
    callsign: "NOJ",
    name: "Kodiak",
    region: "Alaska / N Pacific",
    lat: 57.75,
    lon: -152.5,
    powerKw: 4,
    sourceUrl: "https://tgftp.nws.noaa.gov/fax/hfak.txt",
    frequencies: [
      { id: "noj-2054", assignedHz: 2_054_000 },
      { id: "noj-4298", assignedHz: 4_298_000 },
      { id: "noj-8459", assignedHz: 8_459_000 },
      { id: "noj-12412", assignedHz: 12_412_500 },
    ],
    windows: [
      { startMin: 220, endMin: 358 },
      { startMin: 590, endMin: 1440 },
      { startMin: 0, endMin: 20 },
    ],
    products: [
      { startMin: 220, title: "Test pattern" },
      { startMin: 223, title: "Sea ice analysis" },
      { startMin: 243, title: "Surface analysis" },
      { startMin: 267, title: "24h surface rebroadcast" },
      { startMin: 277, title: "48h surface rebroadcast" },
      { startMin: 287, title: "96h surface rebroadcast" },
      { startMin: 296, title: "Sea state analysis" },
      { startMin: 306, title: "GOES IR satellite" },
      { startMin: 317, title: "500 mb analysis" },
      { startMin: 327, title: "Symbols / schedule" },
      { startMin: 348, title: "Request for comments" },
      { startMin: 358, title: "24h 500 mb forecast" },
      { startMin: 590, title: "Test pattern" },
      { startMin: 593, title: "Surface analysis" },
      { startMin: 617, title: "24h surface forecast" },
      { startMin: 627, title: "24h wind/wave forecast" },
      { startMin: 637, title: "48h surface forecast" },
      { startMin: 647, title: "48h wind/wave forecast" },
      { startMin: 657, title: "Sea ice forecast" },
      { startMin: 677, title: "GOES IR satellite" },
      { startMin: 688, title: "48h wave period" },
      { startMin: 698, title: "48h 500 mb forecast" },
      { startMin: 708, title: "SST analysis" },
      { startMin: 719, title: "Cook Inlet ice" },
      { startMin: 940, title: "Test pattern" },
      { startMin: 943, title: "Sea ice analysis" },
      { startMin: 963, title: "Surface analysis" },
      { startMin: 987, title: "24h surface rebroadcast" },
      { startMin: 997, title: "48h surface rebroadcast" },
      { startMin: 1007, title: "96h surface rebroadcast" },
      { startMin: 1016, title: "Sea state analysis" },
      { startMin: 1026, title: "GOES IR satellite" },
      { startMin: 1037, title: "500 mb analysis" },
      { startMin: 1047, title: "Symbols / schedule" },
      { startMin: 1068, title: "Request for comments" },
      { startMin: 1078, title: "24h 500 mb forecast" },
      { startMin: 1088, title: "48h 500 mb forecast" },
      { startMin: 1290, title: "Test pattern" },
      { startMin: 1293, title: "Surface analysis" },
      { startMin: 1317, title: "24h surface forecast" },
      { startMin: 1337, title: "24h wind/wave forecast" },
      { startMin: 1347, title: "48h surface forecast" },
      { startMin: 1357, title: "48h wind/wave forecast" },
      { startMin: 1367, title: "48h wave period" },
      { startMin: 1377, title: "Sea ice forecast" },
      { startMin: 1387, title: "GOES IR satellite" },
      { startMin: 1397, title: "72h surface forecast" },
      { startMin: 1408, title: "72h wind/wave forecast" },
      { startMin: 1418, title: "72h wave period" },
      { startMin: 1428, title: "96h surface forecast" },
      { startMin: 1438, title: "96h wind/wave forecast" },
      { startMin: 8, title: "96h wave period" },
      { startMin: 18, title: "96h 500 mb forecast" },
    ],
  },
  {
    id: "kvm70",
    callsign: "KVM70",
    name: "Honolulu",
    region: "Central / Tropical Pacific",
    lat: 21.45,
    lon: -158.0,
    powerKw: 4,
    sourceUrl: "https://tgftp.nws.noaa.gov/fax/hfhi.txt",
    frequencies: [
      {
        id: "kvm70-9982",
        assignedHz: 9_982_500,
        windows: [{ startMin: 319, endMin: 956 }],
      },
      { id: "kvm70-11090", assignedHz: 11_090_000 },
      {
        id: "kvm70-16135",
        assignedHz: 16_135_000,
        windows: [
          { startMin: 1039, endMin: 1440 },
          { startMin: 0, endMin: 236 },
        ],
      },
    ],
    windows: [
      { startMin: 319, endMin: 956 },
      { startMin: 1039, endMin: 1440 },
      { startMin: 0, endMin: 236 },
    ],
    products: [
      { startMin: 319, title: "Test pattern" },
      { startMin: 335, title: "Cyclone danger area" },
      { startMin: 355, title: "Streamline analysis" },
      { startMin: 375, title: "Surface analysis" },
      { startMin: 395, title: "East Pacific GOES IR" },
      { startMin: 409, title: "SW Pacific GOES IR" },
      { startMin: 421, title: "24h surface forecast" },
      { startMin: 434, title: "48h surface forecast" },
      { startMin: 447, title: "72h surface forecast" },
      { startMin: 460, title: "Wind/wave analysis" },
      { startMin: 473, title: "24h wind/wave forecast" },
      { startMin: 486, title: "24h wind/wave (OPC)" },
      { startMin: 496, title: "48h surface (OPC)" },
      { startMin: 506, title: "48h wind/wave (OPC)" },
      { startMin: 516, title: "48/96h wave period" },
      { startMin: 526, title: "96h surface rebroadcast" },
      { startMin: 536, title: "96h wind/wave rebroadcast" },
      { startMin: 546, title: "Pacific GOES IR" },
      { startMin: 557, title: "Surface · NE Pacific" },
      { startMin: 570, title: "Surface · NW Pacific" },
      { startMin: 583, title: "Tropical GOES IR" },
      { startMin: 594, title: "Tropical surface analysis" },
      { startMin: 608, title: "24h tropical wind/wave" },
      { startMin: 642, title: "Cyclone danger area" },
      { startMin: 662, title: "48h wind/wave forecast" },
      { startMin: 675, title: "72h wind/wave forecast" },
      { startMin: 688, title: "Sea surface temps" },
      { startMin: 701, title: "24h wind/wave rebroadcast" },
      { startMin: 714, title: "Streamline analysis" },
      { startMin: 734, title: "Surface analysis" },
      { startMin: 754, title: "East Pacific GOES IR" },
      { startMin: 768, title: "SW Pacific GOES IR" },
      { startMin: 780, title: "Schedule part I" },
      { startMin: 800, title: "Schedule part II" },
      { startMin: 820, title: "Symbols / product notice" },
      { startMin: 840, title: "24h tropical surface" },
      { startMin: 850, title: "48h tropical surface" },
      { startMin: 860, title: "72h tropical surface" },
      { startMin: 870, title: "Tropical wave period" },
      { startMin: 880, title: "Tropical sea state" },
      { startMin: 890, title: "24h tropical wind/wave" },
      { startMin: 900, title: "48h tropical wind/wave" },
      { startMin: 910, title: "72h tropical wind/wave" },
      { startMin: 920, title: "Sea state rebroadcast" },
      { startMin: 930, title: "Surface · NE Pacific" },
      { startMin: 943, title: "Surface · NW Pacific" },
      { startMin: 956, title: "Tropical surface analysis" },
      { startMin: 1039, title: "Test pattern" },
      { startMin: 1055, title: "Cyclone danger area" },
      { startMin: 1075, title: "Streamline analysis" },
      { startMin: 1095, title: "Surface analysis" },
      { startMin: 1115, title: "East Pacific GOES IR" },
      { startMin: 1129, title: "SW Pacific GOES IR" },
      { startMin: 1141, title: "24h surface forecast" },
      { startMin: 1154, title: "48h surface forecast" },
      { startMin: 1167, title: "72h surface forecast" },
      { startMin: 1180, title: "Wind/wave analysis" },
      { startMin: 1193, title: "24h wind/wave forecast" },
      { startMin: 1206, title: "24h wind/wave (OPC)" },
      { startMin: 1216, title: "48h surface (OPC)" },
      { startMin: 1226, title: "48h wind/wave (OPC)" },
      { startMin: 1236, title: "48/96h wave period" },
      { startMin: 1246, title: "96h surface rebroadcast" },
      { startMin: 1256, title: "96h wind/wave rebroadcast" },
      { startMin: 1266, title: "Pacific GOES IR" },
      { startMin: 1277, title: "Surface · NE Pacific" },
      { startMin: 1290, title: "Surface · NW Pacific" },
      { startMin: 1303, title: "Tropical GOES IR" },
      { startMin: 1314, title: "Tropical surface analysis" },
      { startMin: 1328, title: "24h tropical wind/wave" },
      { startMin: 1362, title: "Cyclone danger area" },
      { startMin: 1382, title: "48h wind/wave forecast" },
      { startMin: 1395, title: "72h wind/wave forecast" },
      { startMin: 1408, title: "Sea surface temps" },
      { startMin: 1421, title: "24h wind/wave rebroadcast" },
      { startMin: 1434, title: "Streamline analysis" },
      { startMin: 14, title: "Surface analysis" },
      { startMin: 34, title: "East Pacific GOES IR" },
      { startMin: 48, title: "SW Pacific GOES IR" },
      { startMin: 60, title: "Schedule part I" },
      { startMin: 80, title: "Schedule part II" },
      { startMin: 100, title: "Symbols / product notice" },
      { startMin: 120, title: "24h tropical surface" },
      { startMin: 130, title: "48h tropical surface" },
      { startMin: 140, title: "72h tropical surface" },
      { startMin: 150, title: "Tropical wave period" },
      { startMin: 160, title: "Tropical sea state" },
      { startMin: 170, title: "24h tropical wind/wave" },
      { startMin: 180, title: "48h tropical wind/wave" },
      { startMin: 190, title: "72h tropical wind/wave" },
      { startMin: 200, title: "Sea state rebroadcast" },
      { startMin: 210, title: "Surface · NE Pacific" },
      { startMin: 223, title: "Surface · NW Pacific" },
      { startMin: 236, title: "Tropical surface analysis" },
    ],
  },
];

export function getWefaxStation(id: string): WefaxStation | undefined {
  return WEFAX_STATIONS.find((s) => s.id === id);
}

export function formatWefaxKhz(assignedHz: number): string {
  const khz = assignedHz / 1_000;
  return Number.isInteger(khz) ? String(khz) : khz.toFixed(1);
}

/**
 * USB dial frequencies used by FakeRtl so station Tune chips hear fax in Demo.
 * Includes every published carrier (assigned − 1.9 kHz).
 */
export function wefaxDemoCarriersHz(): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const station of WEFAX_STATIONS) {
    for (const freq of station.frequencies) {
      const hz = wefaxTuneHz(freq.assignedHz);
      if (seen.has(hz)) continue;
      seen.add(hz);
      out.push(hz);
    }
  }
  return out;
}
