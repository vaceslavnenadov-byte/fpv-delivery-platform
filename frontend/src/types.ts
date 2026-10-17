export interface Me {
  id: string;
  username: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  groups: string[];
  profile_completed: boolean;
  operator_status: string | null;
  email?: string | null;
  phone?: string | null;
  profile_status?: "pending" | "verified" | "rejected";
  account_status?: "active" | "suspended";
  profile_review_notes?: string | null;
}

export interface Point {
  lat: number;
  lon: number;
  address?: string;
}

export interface Stop extends Point {
  address: string;
  note?: string;
}

export interface RouteInfo {
  base?: Point | null;          // А — база парка (если есть, origin = Б — забор груза, destination = В)
  origin: Point;
  destination: Point;
  distance_m: number;
  return_m?: number | null;
  stops?: Stop[];               // промежуточные точки между Б и В (по порядку)
  max_altitude_m: number;
  checkpoints: { name: string; lat: number; lon: number; kind?: string }[];
  approval_status: string;
  approval_notes: { code: string; ok: boolean; message: string }[];
}

export interface MapBounds {
  north: number;
  south: number;
  west: number;
  east: number;
}

export interface MapLayer {
  id: string;
  name: string;
  description: string;
  content_type: string;
  size_bytes: number;
  width_px: number | null;
  height_px: number | null;
  bounds: MapBounds;
  active: boolean;
  created_at: string;
  image_url: string;
}

export interface Drone {
  id: string;
  serial_number: string;
  registration_number: string;
  manufacturer: string;
  model: string;
  adapter_kind: string;
  max_payload_g: number;
  max_range_m: number;
  status: string;
  health_status: string;
  link_status: string;
  last_seen_at: string | null;
  battery_pct: number | null;
  firmware_version: string | null;
  has_key: boolean;
  key_prefix: string | null;
  blocked_reason: string | null;
  owner_id: string;
  fleet_id?: string | null;
  published?: boolean;
  video_checked_at?: string | null;
  health_checks?: HealthCheck[];
  maintenance?: Maintenance[];
  readiness?: { ready: boolean; reasons: string[] };
}

export interface HealthCheck {
  id: number;
  source: string;
  result: string;
  battery_pct: number | null;
  error_codes: string[];
  details: Record<string, unknown>;
  created_at: string;
}

export interface Maintenance {
  id: number;
  kind: string;
  description: string;
  components: Record<string, string>[];
  health_status_set: string | null;
  created_at: string;
}

export interface Video {
  id: string;
  status: string;
  missing_reason: string | null;
  completion_event_at: string;
  clip_start_at: string | null;
  clip_end_at: string | null;
  segment_count: number;
  review_status: string;
  review_comment: string | null;
  integrity_ok: boolean | null;
  integrity_checked_at: string | null;
  retention_until: string;
  sha256?: string;
  size_bytes?: number;
  has_preview?: boolean;
  order_id?: string;
}

export interface UserBrief {
  id: string;
  display_name: string;
  username: string;
}

export interface Order {
  id: string;
  number: number;
  status: string;
  status_label: string;
  version: number;
  cargo_type: string;
  cargo_description: string;
  cargo_weight_g: number;
  cargo_dims_mm: number[];
  window_start: string;
  window_end: string;
  quote_amount: string | null;
  quote_currency: string;
  drone_id: string;
  customer_id: string;
  operator_id: string | null;
  coordinator_id: string | null;
  tech_ready_at?: string | null;
  map_provider_id?: string | null;   // онлайн-карта, выбранная заказчиком
  tech_ready_by?: UserBrief | null;
  started_at?: string | null;
  created_at: string;
  delivered_at: string | null;
  completed_at: string | null;
  closed_reason: string | null;
  receipt_overdue: boolean;
  route: RouteInfo | null;
  validation_result?: { ok: boolean; checks: { code: string; ok: boolean; message: string }[] };
  recipient_name?: string;
  recipient_phone?: string;
  customer?: UserBrief | null;
  operator?: UserBrief | null;
  coordinator?: UserBrief | null;
  drone?: Drone | null;
  video?: Video | null;
  dispute?: Complaint | null;
}

export interface Telemetry {
  seq: number;
  recorded_at: string;
  lat: number | null;
  lon: number | null;
  alt_m: number | null;
  ground_speed_mps: number | null;
  battery_pct: number | null;
  link_quality: number | null;
  flight_phase: string | null;
  heading_deg?: number | null;
  gnss_fix?: boolean | null;
  extra?: TelemetryExtra;
}

/** Дополнительные показатели сервисного модуля (только отображение). */
export interface TelemetryExtra {
  satellites?: number;
  voltage_v?: number;
  alt_msl_m?: number;
  vspeed_mps?: number;
  wind_mps?: number;
  wind_dir_deg?: number;
  pitch_deg?: number;
  roll_deg?: number;
  flight_mode?: string;
  waypoint_index?: number;
  waypoint_total?: number;
  video_resolution?: string;
  video_fps?: number;
}

export interface Note {
  id: number;
  kind: string;
  severity: string;
  body: string;
  author: UserBrief | null;
  created_at: string;
  attachment_url?: string | null;
}

export interface Score {
  preliminary: boolean;
  total?: number;
  final_points?: number;
  total_with_adjustments?: number;
  time_window: { points: number; deviation_minutes: number | null; max?: number; note?: string };
  result: {
    points: number;
    max: number;
    items: Record<string, number>;
    facts: Record<string, boolean>;
    upheld_complaints: number;
    penalty: number;
    video_status: string | null;
  };
  adjustments?: { points: number; reason: string; created_at: string }[];
  rule_version?: number;
}

export interface Notification {
  id: number;
  kind: string;
  severity: string;
  title: string;
  body: string;
  order_id: string | null;
  read: boolean;
  created_at: string;
}

export interface HistoryItem {
  id: number;
  from: string | null;
  to: string;
  actor_kind: string;
  actor: string | null;
  reason: string;
  created_at: string;
}

export interface Fleet {
  id: string;
  name: string;
  technician_id: string;
  base: Point;
  active: boolean;
  drone_count: number;
  created_at: string;
  zone_id?: string | null;
}

export interface AvailableDrone {
  id: string;
  registration_number: string;
  model: string;
  manufacturer: string;
  max_payload_g: number;
  max_range_m: number;
  link_status: string;
  battery_pct: number | null;
  busy: boolean;
  fleet: { id: string; name: string; zone_id?: string | null; base: Point };
}

export interface DroneConnection {
  link_status: string;
  last_seen_at: string | null;
  battery_pct: number | null;
  telemetry: null | {
    recorded_at: string; age_s: number; lat: number | null; lon: number | null; alt_m: number | null;
    battery_pct: number | null; link_quality: number | null; gnss_fix: boolean | null; flight_phase: string | null;
  };
  video: {
    sources: { id: string; kind: string; label: string }[];
    active: string | null;
    selected: string | null;
    check_active: boolean;
    check_until: string | null;
    checked_at: string | null;
    receiving: boolean;
  };
  has_key: boolean;
  published: boolean;
}

/** Претензия (спор по доставке) или апелляция оценки. */
export interface Complaint {
  id: number;
  order_id: string;
  kind: "dispute" | "score_appeal";
  status: "open" | "upheld" | "dismissed";
  description: string;
  resolution: string | null;
  points_adjustment: number | null;
  author: UserBrief | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by: UserBrief | null;
  coordinator_verdict: "upheld" | "dismissed" | null;
  coordinator_comment: string | null;
  coordinator: UserBrief | null;
  coordinator_at: string | null;
}
