import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { Credential } from "@/api/http";
import { StaffAuthContext } from "@/auth/useStaffAuth";
import { reviewApi } from "@/api/review";
import type { ReviewerProfile } from "@/api/types";

type ReviewerCredential = Extract<Credential, { kind: "reviewer" }>;

// Staff sign-in for the console. The reviewer token is held in memory only (never in storage):
// reloading or closing the tab signs out, the same rule as the server-rendered review page.

export function StaffAuthProvider({ children }: { children: ReactNode }) {
  const [reviewer, setReviewer] = useState<ReviewerCredential | null>(null);
  const [profile, setProfile] = useState<ReviewerProfile | null>(null);

  const signIn = useCallback(async (organizationId: string, token: string) => {
    const credential: ReviewerCredential = { kind: "reviewer", organizationId: organizationId.trim(), token: token.trim() };
    const me = await reviewApi.me(credential);
    setReviewer(credential);
    setProfile(me);
    return me;
  }, []);

  const signOut = useCallback(() => {
    setReviewer(null);
    setProfile(null);
  }, []);

  const value = useMemo(() => ({ reviewer, profile, signIn, signOut }), [reviewer, profile, signIn, signOut]);
  return <StaffAuthContext.Provider value={value}>{children}</StaffAuthContext.Provider>;
}
