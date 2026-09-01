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
