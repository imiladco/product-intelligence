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
  /** Whether this provider can list and verify resources at all.
   *
   *  Independent of `status`: a provider with no resource discovery offers no
   *  picker however healthy its connection is. The backend is the source of
   *  truth, so the frontend never keeps its own list of what each provider
   *  supports. */
  supports_resource_selection: boolean;
}

/** Response of GET /api/projects/{id}/integrations/{provider}/authorize.
 *  Django builds the URL; the frontend only follows it. */
export interface AuthorizationStart {
  authorization_url: string;
}

/** One selectable external resource, in provider-neutral terms.
 *
 *  `id` is the provider's own identifier and is sent back verbatim; the
 *  browser never builds or parses one. `resource_type` and `group_label` are
 *  display-only and may be empty — a provider whose resources have no grouping
 *  leaves `group_label` blank everywhere, and the list renders flat. */
export interface DiscoveredResource {
  id: string;
  label: string;
  group_label: string;
  resource_type: string;
}

/** Response of GET /api/projects/{id}/integrations/{provider}/resources.
 *  `truncated` is true when the account has more than the backend will page
 *  through, so the list is usable but not exhaustive. */
export interface DiscoveredResources {
  resources: DiscoveredResource[];
  truncated: boolean;
}
