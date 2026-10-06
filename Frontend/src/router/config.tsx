import type { RouteObject } from "react-router-dom";
import { Navigate } from "react-router-dom";
import RequireStaff from "@/auth/RequireStaff";
import LegacySessionRedirect from "./LegacySessionRedirect";
import NotFound from "../pages/NotFound";
import SignInPage from "../pages/signin/page";
import ConsoleHome from "../pages/console/page";
import NewVerification from "../pages/verify/new/page";
import VerificationSession from "../pages/verify/session/page";
import VerificationResult from "../pages/verify/result/page";
import ReviewQueue from "../pages/review/page";
import ReviewSession from "../pages/review/session/page";
import Developers from "../pages/developers/page";

// Routes are grouped by who uses them, because each group holds a different credential:
//   staff (reviewer token, signed in at /signin)   /console, /review, /developers, /verify/new
//   applicant (session client token in the link)    /verify/:sessionId, /verify/:sessionId/done
// API keys never reach a browser in production: your server creates sessions and hands the
// applicant a /verify/:sessionId link.

const routes: RouteObject[] = [
  { path: "/", element: <Navigate to="/console" replace /> },
  { path: "/signin", element: <SignInPage /> },
  {
    element: <RequireStaff />,
    children: [
      { path: "/console", element: <ConsoleHome /> },
      { path: "/review", element: <ReviewQueue /> },
      { path: "/review/:sessionId", element: <ReviewSession /> },
      { path: "/developers", element: <Developers /> },
      { path: "/verify/new", element: <NewVerification /> },
    ],
  },
  { path: "/verify/:sessionId", element: <VerificationSession /> },
  { path: "/verify/:sessionId/done", element: <VerificationResult /> },
  // Earlier paths, kept so old links keep working.
  { path: "/verify/session/:sessionId", element: <LegacySessionRedirect /> },
  { path: "/verify/result", element: <Navigate to="/console" replace /> },
  { path: "*", element: <NotFound /> },
];

export default routes;
