import { createContext, useContext } from "react";
import type { Credential } from "@/api/http";
import type { OrganizationInfo, ReviewerProfile } from "@/api/types";

type ReviewerCredential = Extract<Credential, { kind: "reviewer" }>;
type ApiKeyCredential = Extract<Credential, { kind: "apiKey" }>;

interface StaffAuth {
  reviewer: ReviewerCredential | null;
  profile: ReviewerProfile | null;
  signIn: (organizationId: string, token: string) => Promise<ReviewerProfile>;
  signOut: () => void;
  /** An API key for the client API (sessions, keys, webhooks), connected after sign-in. */
  apiKey: ApiKeyCredential | null;
  organization: OrganizationInfo | null;
  connectApiKey: (key: string) => Promise<OrganizationInfo>;
  disconnectApiKey: () => void;
}

export const StaffAuthContext = createContext<StaffAuth | null>(null);

export function useStaffAuth(): StaffAuth {
  const value = useContext(StaffAuthContext);
  if (!value) throw new Error("useStaffAuth must be used inside StaffAuthProvider");
  return value;
}

/** The signed-in reviewer's credential; only call inside routes guarded by RequireStaff. */
export function useReviewer(): { credential: ReviewerCredential; profile: ReviewerProfile; signOut: () => void } {
  const { reviewer, profile, signOut } = useStaffAuth();
  if (!reviewer || !profile) throw new Error("useReviewer used outside a RequireStaff route");
  return { credential: reviewer, profile, signOut };
}

/** The connected API key, or null; pages that need one show <ApiKeyGate>. */
export function useApiKey(): { apiKey: ApiKeyCredential | null; organization: OrganizationInfo | null;
                               disconnect: () => void; has: (scope: string) => boolean } {
  const { apiKey, organization, disconnectApiKey } = useStaffAuth();
  const scopes = organization?.credential.scopes ?? [];
  return { apiKey, organization, disconnect: disconnectApiKey, has: (scope) => scopes.includes(scope) };
}
