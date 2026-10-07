import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { Credential } from "@/api/http";
import { StaffAuthContext } from "@/auth/useStaffAuth";
import { reviewApi } from "@/api/review";
import { kycApi } from "@/api/client";
import type { OrganizationInfo, ReviewerProfile } from "@/api/types";

type ReviewerCredential = Extract<Credential, { kind: "reviewer" }>;
type ApiKeyCredential = Extract<Credential, { kind: "apiKey" }>;

// Staff sign-in for the console. The reviewer token and any API key are held in memory only (never
// in storage): reloading or closing the tab signs out, the same rule as the server-rendered review page.
// The API key is for the client API (sessions, keys, webhooks); review endpoints never accept it.

export function StaffAuthProvider({ children }: { children: ReactNode }) {
  const [reviewer, setReviewer] = useState<ReviewerCredential | null>(null);
  const [profile, setProfile] = useState<ReviewerProfile | null>(null);
  const [apiKey, setApiKey] = useState<ApiKeyCredential | null>(null);
  const [organization, setOrganization] = useState<OrganizationInfo | null>(null);

  const signIn = useCallback(async (organizationId: string, token: string) => {
    const credential: ReviewerCredential = { kind: "reviewer", organizationId: organizationId.trim(), token: token.trim() };
    const me = await reviewApi.me(credential);
    setReviewer(credential);
    setProfile(me);
    return me;
  }, []);

  const disconnectApiKey = useCallback(() => {
    setApiKey(null);
    setOrganization(null);
  }, []);

  const signOut = useCallback(() => {
    setReviewer(null);
    setProfile(null);
    disconnectApiKey();
  }, [disconnectApiKey]);

  const connectApiKey = useCallback(async (key: string) => {
    if (!reviewer) throw new Error("Sign in first.");
    const credential: ApiKeyCredential = { kind: "apiKey", organizationId: reviewer.organizationId, key: key.trim() };
    const info = await kycApi.organization(credential);
    setApiKey(credential);
    setOrganization(info);
    return info;
  }, [reviewer]);

  const value = useMemo(() => ({ reviewer, profile, signIn, signOut, apiKey, organization, connectApiKey, disconnectApiKey }),
    [reviewer, profile, signIn, signOut, apiKey, organization, connectApiKey, disconnectApiKey]);
  return <StaffAuthContext.Provider value={value}>{children}</StaffAuthContext.Provider>;
}
