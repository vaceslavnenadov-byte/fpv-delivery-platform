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
}

export interface Point {
  lat: number;
  lon: number;
  address?: string;
}

export interface RouteInfo {
  origin: Point;
  destination: Point;
  distance_m: number;
  max_altitude_m: number;
  checkpoints: { name: string; lat: number; lon: number }[];
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
