import { useState } from "react";
import { ArrowLeft, Check, CheckCircle2, ChevronRight, FileText, Fingerprint, IdCard, LockKeyhole, Scan, ScanFace, ShieldCheck, Sparkles, UserCheck } from "lucide-react";

/** An illustrative KYC mobile application preview modelled on modern dark fintech interfaces. */
export default function VerificationPreview() {
  const [activeScreen, setActiveScreen] = useState<"scan" | "metrics">("scan");

  return (
    <div className="verification-preview" aria-label="Illustration of the OKX-inspired identity verification app">
      <div className="preview-orbit preview-orbit-one" aria-hidden="true" />
      <div className="preview-orbit preview-orbit-two" aria-hidden="true" />

      <div className="preview-phone">
        {/* Notch / Speaker bar */}
        <div className="phone-camera" aria-hidden="true" />

        {/* Top Status & Balance / Session pill */}
        <div className="phone-top-bar">
          <span className="phone-live-tag"><span className="status-dot animate-pulse" />Live KYC Session</span>
          <span className="phone-session-id">#VX-8921 <ChevronRight size={12} /></span>
        </div>

        {/* Screen switcher tab */}
        <div className="phone-view-tabs" role="tablist">
          <button
            type="button"
            className={activeScreen === "scan" ? "active" : ""}
            onClick={() => setActiveScreen("scan")}
          >
            Live Scan
          </button>
          <button
            type="button"
            className={activeScreen === "metrics" ? "active" : ""}
            onClick={() => setActiveScreen("metrics")}
          >
            Verification Data
          </button>
        </div>

        {activeScreen === "scan" ? (
          <div className="phone-screen-content">
            <div className="phone-stage-header">
              <span className="phone-eyebrow">STEP 02 OF 03</span>
              <h4>Cambodia National ID</h4>
              <p>Position front of card within target frame</p>
            </div>

            {/* Viewfinder area */}
            <div className="phone-scan" aria-hidden="true">
              <span className="scan-corner top-left" />
              <span className="scan-corner top-right" />
              <span className="scan-corner bottom-left" />
              <span className="scan-corner bottom-right" />
              
              <div className="scan-id-card-silhouette">
                <div className="card-mock-photo">
                  <Fingerprint size={28} strokeWidth={1.5} />
                </div>
                <div className="card-mock-lines">
                  <span className="line-long" />
                  <span className="line-mid" />
                  <span className="line-short" />
                </div>
              </div>

              <div className="scan-beam" />
            </div>

            {/* Live real-time check badges */}
            <div className="phone-checks-list">
              <div className="phone-check">
                <span><IdCard size={15} />Smart Card OCR</span>
                <span className="phone-badge-green"><Check size={12} />99.8%</span>
              </div>
              <div className="phone-check">
                <span><ScanFace size={15} />3D Liveness Check</span>
                <span className="phone-badge-green"><Check size={12} />0.994</span>
              </div>
              <div className="phone-check">
                <span><ShieldCheck size={15} />Registry Sync</span>
                <span className="phone-badge-green"><Check size={12} />Matched</span>
              </div>
            </div>

            <div className="phone-complete">
              <CheckCircle2 size={15} />
              <span>Identity Verified</span>
            </div>
          </div>
        ) : (
          <div className="phone-screen-content">
            <div className="phone-stage-header">
              <span className="phone-eyebrow">EXTRACTION TELEMETRY</span>
              <h4>Applicant Details</h4>
              <p>Cryptographically validated evidence</p>
            </div>

            <div className="phone-metrics-list">
              <div className="phone-metric-row">
                <span className="metric-label">Full Name</span>
                <span className="metric-val">SOK SAN (សុខ សាន)</span>
              </div>
              <div className="phone-metric-row">
                <span className="metric-label">ID Number</span>
                <span className="metric-val">010892471</span>
              </div>
              <div className="phone-metric-row">
                <span className="metric-label">Expiry Date</span>
                <span className="metric-val">2031-10-09 (Valid)</span>
              </div>
              <div className="phone-metric-row">
                <span className="metric-label">Biometric Distance</span>
                <span className="metric-val text-accent">0.182 (High match)</span>
              </div>
              <div className="phone-metric-row">
                <span className="metric-label">Antispoof Score</span>
                <span className="metric-val text-accent">0.012 (Pass)</span>
              </div>
            </div>

            <div className="phone-complete">
              <LockKeyhole size={14} />
              <span>End-to-End Encrypted</span>
            </div>
          </div>
        )}

        {/* Bottom App Navigation Bar (styled like OKX app bottom bar) */}
        <div className="phone-bottom-nav" aria-hidden="true">
          <div className="nav-item"><FileText size={16} /><span>Docs</span></div>
          <div className="nav-item"><ShieldCheck size={16} /><span>Trust</span></div>
          <div className="nav-item nav-center"><Scan size={18} /></div>
          <div className="nav-item"><UserCheck size={16} /><span>Review</span></div>
          <div className="nav-item"><Sparkles size={16} /><span>Audit</span></div>
        </div>

        <div className="phone-home-indicator" aria-hidden="true" />
      </div>

      <div className="preview-floating-badge">
        <span><ShieldCheck size={22} /></span>
        <div>
          <strong>Enterprise Grade Security</strong>
          <small>99.9% Uptime · Sub-second Verification</small>
        </div>
      </div>
    </div>
  );
}
