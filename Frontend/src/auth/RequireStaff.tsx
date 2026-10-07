import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useStaffAuth } from "@/auth/useStaffAuth";

/** Layout route for console pages: sends anyone not signed in to /signin and back afterwards. */
export default function RequireStaff() {
  const { reviewer } = useStaffAuth();
  const location = useLocation();
  if (!reviewer) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/signin?next=${encodeURIComponent(next)}`} replace />;
  }
  return <Outlet />;
}
