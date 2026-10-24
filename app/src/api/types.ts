export type Severity = "info" | "warn" | "critical";
export type Risk = "read" | "low" | "reversible" | "high" | "final";

export interface Status {
  version: string;
  user_name: string;
  autonomy: number;
  halted: boolean;
  autonomous_last_hour: number;
  autonomous_limit: number;
  undo_window_s: number;
  llm: { provider: string; model: string; available: boolean };
  costs: {
    today_eur: number;
    month_eur: number;
    daily_limit_eur: number;
    monthly_limit_eur: number;
    model: string;
  };
  alerts: Record<Severity, number>;
  pending_approvals: number;
  monitor: {
    last_scan: number | null;
    last_error: string | null;
    sysmon: string;
    watched_folders: string[];
    interval_s: number;
  };
  voice: VoiceStatus;
  server: { configured: boolean; error: string | null };
  warnings?: { key: string; title: string; message: string }[];
  night?: boolean;
  time: number;
}

export interface VoiceStatus {
  state: string;
  enabled: boolean;
  capabilities: Record<string, boolean>;
  error: string | null;
  last_transcript: string | null;
  wakeword: boolean;
  clap: boolean;
  active?: boolean;
  stt_ready?: boolean;
  tts_engine?: "edge" | "piper" | "browser";
  tts_voice?: string;
  stt_provider?: "elevenlabs" | "local";
  stt_error?: string | null;
}

export interface SystemInfo {
  os: string;
  cpu_percent: number;
  cpu_count: number;
  memory_total_gb: number;
  memory_percent: number;
  disk_percent: number;
  disk_free_gb: number;
  uptime_h: number;
  process_count: number;
}

export interface Approval {
  id: string;
  created: number;
  expires: number;
  action: string;
  params: Record<string, unknown>;
  source: string;
  actor: string;
  risk: Risk;
  reason: string;
  preview: string;
  tainted: boolean;
  status: string;
  requires_pin: boolean;
}

export interface ResponsePlugin {
  name: string;
  file: string;
  origin: "builtin" | "user";
  sha256: string;
  status: "loaded" | "pending_approval" | "disabled" | "error";
  error: string | null;
  description: string | null;
  risk_level: string | null;
  requires_pin: boolean | null;
  priority: number;
}

export interface ResponseMatch {
  action: string;
  name: string;
  description: string;
  risk_level: string;
  requires_pin: boolean;
}

export interface ActionOutcome {
  status: "executed" | "pending" | "denied" | "failed";
  action: string;
  message: string;
  data: unknown;
  approval_id: string | null;
  undo_id: string | null;
  untrusted: boolean;
}

export interface Alert {
  id: number;
  ts: number;
  rule: string;
  severity: Severity;
  title: string;
  detail: Record<string, unknown>;
  status: "new" | "ack";
  response: { action: string; status: string; message: string } | null;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  source: string;
  action: string;
  params: string;
  decision: string;
  result: string;
  detail: string;
  hash: string;
}

export interface Memory {
  id: number;
  text: string;
  category: string;
  source: string;
  created: number;
  updated: number;
  score?: number;
}

export interface Device {
  mac: string;
  ip: string;
  hostname: string | null;
  label: string | null;
  trusted: boolean;
  first_seen: number;
  last_seen: number;
  vendor: string | null;
  ports: { port: number; service: string; risky: boolean }[] | null;
  ports_checked: number | null;
}

export interface ProcessRow {
  pid: number;
  ppid: number;
  name: string;
  exe: string;
  username: string;
  cpu: number;
  memory_mb: number;
  started: number;
}

export interface PortRow {
  address: string;
  port: number;
  pid: number;
  process: string;
  public: boolean;
}

export interface Reminder {
  id: number;
  text: string;
  due: number;
  created: number;
  done: number;
}

export interface Program {
  name: string;
  title: string;
  path: string;
}

export interface Routine {
  name: string;
  title: string;
  origin: string;
  steps: { action: string; params: Record<string, unknown> }[];
}

export interface AgentInfo {
  name: string;
  title: string;
  description: string;
  running: boolean;
}

export interface AgentReport {
  id: number;
  agent: string;
  ts: number;
  title: string;
  body: string;
}

export interface Plugin {
  name: string;
  version: string;
  description: string;
  routines: string[];
  feeds: number;
  enabled: boolean;
  error: string | null;
}

export interface BackendEvent {
  id: number;
  kind: string;
  ts: number;
  data: Record<string, unknown>;
}

export interface ChatEvent {
  type: "text" | "tool" | "memory" | "done" | "error";
  text?: string;
  name?: string;
  status?: string;
  message?: string;
  approval_id?: string | null;
  undo_id?: string | null;
  tainted?: boolean;
  conversation?: string;
}
