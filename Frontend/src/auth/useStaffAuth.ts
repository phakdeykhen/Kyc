import { createContext, useContext } from "react";
import type { Credential } from "@/api/http";
import type { ReviewerProfile } from "@/api/types";

type ReviewerCredential = Extract<Credential, { kind: "reviewer" }>;

interface StaffAuth {
  reviewer: ReviewerCredential | null;
  profile: ReviewerProfile | null;
  signIn: (organizationId: string, token: string) => Promise<ReviewerProfile>;
  signOut: () => void;
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
