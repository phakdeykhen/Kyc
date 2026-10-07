import { Navigate, useParams } from "react-router-dom";

/** /verify/session/:sessionId (earlier path) → /verify/:sessionId. */
export default function LegacySessionRedirect() {
  const { sessionId = "" } = useParams();
  return <Navigate to={`/verify/${encodeURIComponent(sessionId)}`} replace />;
}
