// Hand-maintained mirror of the DRF serializers. Small enough that a codegen
// pipeline would cost more than it saves; keep in step with apps/api.

export type Role = "owner" | "member";

export interface User {
  id: number;
  email: string;
  name: string;
  date_joined: string;
}

export interface Workspace {
  id: number;
  name: string;
  slug: string;
  role: Role | null;
  created_at: string;
}

export interface Project {
  id: number;
  workspace: number;
  name: string;
  website_url: string;
  created_at: string;
  updated_at: string;
}

export interface Session {
  user: User;
  workspaces: Workspace[];
}

/** The single error envelope every API error uses. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail?: Record<string, string[]>;
  };
}

// --- Integrations -----------------------------------------------------------

/** Stored states, plus `not_connected`, which the API synthesizes when no
 *  connection row exists for a (project, provider) pair. */
export type IntegrationStatus =
  | "not_connected"
  | "pending_authorization"
  | "awaiting_resource_selection"
  | "connected"
  | "error"
  | "reauth_required"
  | "disconnected";

export interface IntegrationConnection {
  id: number;
  status: IntegrationStatus;
  external_resource_id: string;
  external_resource_label: string;
  google_account_email: string;
  last_health_check_at: string | null;
  last_successful_check_at: string | null;
  last_error_code: string;
  last_error_message: string;
  created_at: string;
  updated_at: string;
}

/** One row of the Integrations page. The provider list comes from the backend
 *  catalog; the frontend never keeps its own copy. */
export interface IntegrationEntry {
  provider: string;
  display_name: string;
  description: string;
  status: IntegrationStatus;
  connection: IntegrationConnection | null;
}

/** Response of GET /api/projects/{id}/integrations/{provider}/authorize.
 *  Django builds the URL; the frontend only follows it. */
export interface AuthorizationStart {
  authorization_url: string;
}
