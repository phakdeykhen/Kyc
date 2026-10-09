import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  ExternalLink,
  Globe,
  LockKeyhole,
  Menu,
  QrCode,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  XCircle,
} from "lucide-react";
import Brand from "@/components/feature/Brand";
import SiteFooter from "@/components/feature/SiteFooter";
import VerificationPreview from "@/components/feature/VerificationPreview";
import { useStaffAuth } from "@/auth/useStaffAuth";

// Section 2 feature cards (using cropped 3D icons from OKX Page 1)
const partnerFeatures = [
  {
    icon: "/assets/okx/sec2_reliability.png",
    title: "Proven reliability",
    description:
      "Trade zero doubt for total confidence. Over 10M+ verifications processed with 99.9% uptime to keep you in control through every market move.",
  },
  {
    icon: "/assets/okx/sec2_reserves.png",
    title: "Transparent compliance",
    description:
      "Keep applicant records secure and auditable through our Proof of Compliance. Need support? Our compliance engineers are here 24/7.",
  },
  {
    icon: "/assets/okx/sec2_deposits.png",
    title: "Easy integration",
    description:
      "Make instant, seamless integrations. Pre-built applicant links, drop-in camera SDKs, or high-throughput REST APIs.",
  },
  {
    icon: "/assets/okx/sec2_verification.png",
    title: "Secure verification",
    description:
      "Verify identity in minutes with photo ID and active 3D face liveness. Once set up, unlock top-of-the-chain compliance features.",
  },
];

// Section 3 coverage ticker cards (modeled on OKX Page 1 crypto price tickers)
const tickerCards = [
  {
    id: "kh-id",
    name: "Cambodia National ID",
    code: "KH-NID",
    flag: "🇰🇭",
    accuracy: "99.8%",
    latency: "< 1.1s",
    badge: "+0.4% speed",
    badgeType: "positive",
    engine: "Khmer OCR + Smart Card",
  },
  {
    id: "icao-pass",
    name: "ICAO e-Passport",
    code: "PASSPORT",
    flag: "🌐",
    accuracy: "99.9%",
    latency: "< 820ms",
    badge: "99.9% Pass",
    badgeType: "positive",
    engine: "MRZ 9303 & NFC Chip",
  },
  {
    id: "kh-nssf",
    name: "NSSF Member Card",
    code: "KH-NSSF",
    flag: "🇰🇭",
    accuracy: "Verified",
    latency: "< 1.0s",
    badge: "Gov Sync",
    badgeType: "neutral",
    engine: "Social Security Registry",
  },
  {
    id: "liveness-3d",
    name: "3D Face Liveness",
    code: "LIVENESS",
    flag: "👤",
    accuracy: "0.012 Risk",
    latency: "< 450ms",
    badge: "Depth Pass",
    badgeType: "positive",
    engine: "Active Antispoofing ONNX",
  },
  {
    id: "driver-lic",
    name: "Driver's License",
    code: "DL-AUTO",
    flag: "🚗",
    accuracy: "100%",
    latency: "< 920ms",
    badge: "PDF417",
    badgeType: "positive",
    engine: "Barcode & Vision Parser",
  },
  {
    id: "residence-card",
    name: "Residence Permit",
    code: "RES-CARD",
    flag: "🇪🇺",
    accuracy: "99.7%",
    latency: "< 890ms",
    badge: "Passed",
    badgeType: "positive",
    engine: "Security Hologram Check",
  },
];

// Section 4 gateway cards (using cropped 3D icons from OKX Page 2)
const gatewayCards = [
  {
    icon: "/assets/okx/sec4_trade.png",
    title: "Document OCR",
    description: "Extract Khmer and Latin ID fields instantly at sub-second speeds with high accuracy.",
    btnText: "Extract now",
    link: "/verify/new",
  },
  {
    icon: "/assets/okx/sec4_bots.png",
    title: "3D Face Liveness",
    description: "Automate biometric checks with precision, stopping deepfakes and printed masks like clockwork.",
    btnText: "Explore liveness",
    link: "/verify/new",
  },
  {
    icon: "/assets/okx/sec4_earn.png",
    title: "Government Lookup",
    description: "Cross-reference official citizen registries for authoritative identity verification.",
    btnText: "Test lookup",
    link: "/console",
  },
  {
    icon: "/assets/okx/sec4_copy.png",
    title: "Review Console",
    description: "Empower fraud analysis teams with split-screen case review, fraud flags, and forensic audit trails.",
    btnText: "Open console",
    link: "/review",
  },
];

// Section 5 inspection rows (matching OKX orderbook table layout)
const telemetryRows = [
  {
    applicant: "SOK SAN",
    docType: "Cambodia National ID",
    confidence: "99.8%",
    bidVal: "-0.0030",
    askVal: "0.0030",
    biometricScore: "0.994 Match",
    latency: "420ms",
    reviewer: "Auto-AI",
    status: "approved",
  },
  {
    applicant: "CHAN THY",
    docType: "ICAO e-Passport",
    confidence: "99.9%",
    bidVal: "0.9980",
    askVal: "0.9990",
    biometricScore: "0.989 Match",
    latency: "380ms",
    reviewer: "Auto-AI",
    status: "approved",
  },
  {
    applicant: "LIM HENG",
    docType: "NSSF Member Card",
    confidence: "99.2%",
    bidVal: "-0.329",
    askVal: "-0.104",
    biometricScore: "Glint Flag",
    latency: "610ms",
    reviewer: "Queue",
    status: "review",
  },
  {
    applicant: "VICHET KEO",
    docType: "Cambodia Driver License",
    confidence: "99.5%",
    bidVal: "-0.0030",
    askVal: "0.0030",
    biometricScore: "0.991 Match",
    latency: "510ms",
    reviewer: "Auto-AI",
    status: "approved",
  },
  {
    applicant: "SOPHEA NOU",
    docType: "International Residence",
    confidence: "99.7%",
    bidVal: "0.9950",
    askVal: "0.9970",
    biometricScore: "0.995 Match",
    latency: "450ms",
    reviewer: "Auto-AI",
    status: "approved",
  },
];

// Section 6 FAQ items
const faqList = [
  {
    question: "What products does Verix KYC provide?",
    answer:
      "Verix provides an end-to-end identity verification suite designed for high-growth fintechs, crypto exchanges, and banks. It includes automated Document OCR (specialized for Khmer national IDs and ICAO passports), active real-time 3D face liveness detection, government citizen database validation, and a forensic manual review console with full audit logging.",
  },
  {
    question: "How do I verify Cambodian and international identity documents?",
    answer:
      "Verix supports front and back scanning of Cambodian National Smart IDs, Passports, and NSSF cards, alongside ICAO 9303 international passports, driver's licenses, and residence permits. Our deep learning vision engine automatically corrects rotation, filters glare, and extracts fields into structured JSON in under 800 milliseconds.",
  },
  {
    question: "How does active 3D face liveness detection protect against deepfakes?",
    answer:
      "Our biometric pipeline employs real-time 3D facial mesh reconstruction (BFM / TDDFA) and texture antispoofing neural networks. It verifies natural head movement challenges, eye blinks, depth maps, and screen reflection patterns, preventing paper cutouts, replay videos, 3D silicone masks, and generative AI deepfakes.",
  },
  {
    question: "Why should financial institutions and Web3 platforms trust Verix KYC?",
    answer:
      "Verix is architected with bank-grade security: AES-256 encrypted evidence storage, strict SOC 2 Type II controls, zero-knowledge retention options, and immutable reviewer audit logs. We guarantee 99.9% API uptime with sub-second processing latency.",
  },
  {
    question: "How can developers integrate Verix via APIs and SDKs?",
    answer:
      "You can integrate Verix in minutes using our official Python SDK, TypeScript client, or standard REST API endpoints. You can also generate one-click secure applicant links that work seamlessly on any iOS or Android browser with zero app installation required.",
  },
];

export default function HomePage() {
  const { reviewer } = useStaffAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [openFaq, setOpenFaq] = useState<number | null>(null);
  const [activeTelemetryTab, setActiveTelemetryTab] = useState<"stream" | "ocr" | "biometrics">("stream");

  return (
    <div className="okx-landing-page">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>

      {/* OKX-Style Top Header */}
      <header className="okx-header">
        <div className="okx-header-inner">
          {/* Brand Logo */}
          <Link to="/" className="okx-header-brand" aria-label="Verix KYC home">
            <Brand />
          </Link>

          {/* Navigation Links */}
          <nav className="okx-header-nav" aria-label="Primary navigation">
            <Link to="/verify/new" className="nav-link font-medium">
              Start KYC
            </Link>
            <a href="#coverage" className="nav-link">
              Coverage <ChevronDown size={12} className="opacity-60" />
            </a>
            <a href="#platform" className="nav-link">
              Platform
            </a>
            <a href="#terminal" className="nav-link">
              Forensic Radar
            </a>
            <Link to="/review" className="nav-link">
              Review Queue
            </Link>
            <Link to="/developers" className="nav-link">
              Developers
            </Link>
          </nav>

          {/* Search Bar (OKX Header style) */}
          <div className="okx-header-search">
            <Search size={14} className="search-icon" />
            <input
              type="text"
              placeholder="Search documents, APIs, coverage..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search documents and features"
            />
          </div>

          {/* Action Buttons */}
          <div className="okx-header-actions">
            <Link
              to={reviewer ? "/console" : "/signin"}
              className="okx-btn-login"
            >
              {reviewer ? "Console" : "Log in"}
            </Link>
            <Link
              to="/verify/new"
              className="okx-pill-btn okx-pill-btn-white"
            >
              Sign up
            </Link>
            
            {/* Download / QR demo toggle button */}
            <button
              type="button"
              className="okx-icon-btn"
              title="Scan to test mobile demo"
              aria-label="Open mobile QR code"
              onClick={() => setQrModalOpen(true)}
            >
              <QrCode size={18} />
            </button>

            {/* Language / Region pill */}
            <div className="okx-lang-selector" title="Language: English / USD">
              <Globe size={15} />
            </div>

            {/* Mobile menu toggle */}
            <button
              type="button"
              className="okx-mobile-toggle"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMenuOpen(!menuOpen)}
            >
              {menuOpen ? <X size={22} /> : <Menu size={22} />}
            </button>
          </div>
        </div>

        {/* Mobile slide-down navigation */}
        {menuOpen && (
          <nav className="okx-mobile-nav" aria-label="Mobile menu">
            <Link to="/verify/new" onClick={() => setMenuOpen(false)}>
              Start KYC Verification
            </Link>
            <a href="#coverage" onClick={() => setMenuOpen(false)}>
              Document Coverage
            </a>
            <a href="#platform" onClick={() => setMenuOpen(false)}>
              Platform Features
            </a>
            <a href="#terminal" onClick={() => setMenuOpen(false)}>
              Live Inspector Terminal
            </a>
            <Link to="/review" onClick={() => setMenuOpen(false)}>
              Review Queue
            </Link>
            <Link to="/developers" onClick={() => setMenuOpen(false)}>
              Developer Hub
            </Link>
            <Link to={reviewer ? "/console" : "/signin"} onClick={() => setMenuOpen(false)}>
              {reviewer ? "Open Console" : "Log in"}
            </Link>
          </nav>
        )}
      </header>

      {/* Main Content */}
      <main id="main-content">
        {/* ========================================================= */}
        {/* HERO SECTION (Modeled on OKX Page 1 Hero)                  */}
        {/* ========================================================= */}
        <section className="okx-hero-section content-width">
          <div className="okx-hero-copy">
            <h1 className="okx-hero-heading">
              Faster, better,<br />
              stronger than your<br />
              average KYC provider
            </h1>
            <p className="okx-hero-subheading">
              Get started with Verix KYC today and benefit from instant Khmer &amp; international OCR, sub-second 3D face liveness, and top-notch enterprise compliance.
            </p>

            <div className="okx-hero-cta-row">
              <Link to="/verify/new" className="okx-pill-btn okx-pill-btn-white okx-pill-btn-lg">
                Sign up
              </Link>
              <button
                type="button"
                className="okx-pill-btn okx-pill-btn-dark okx-pill-btn-lg"
                onClick={() => setQrModalOpen(true)}
              >
                <QrCode size={18} />
                <span>Download app</span>
              </button>
            </div>

            {/* Partners Logo Row (OKX Sponsor banner) */}
            <div className="okx-partners-row">
              <img
                src="/assets/okx/partners.png"
                alt="Tribeca Festival, McLaren Formula 1 Team, Manchester City"
                className="okx-partners-img"
              />
            </div>
          </div>

          {/* Right Column: Sleek Phone Mockup with KYC flow */}
          <div className="okx-hero-visual">
            <VerificationPreview />
          </div>
        </section>

        {/* ========================================================= */}
        {/* SECTION 2: Your secure partner for identity verification  */}
        {/* Modeled on OKX Page 1 Section 2                           */}
        {/* ========================================================= */}
        <section id="platform" className="okx-section content-width">
          <div className="okx-section-header-center">
            <h2>Your secure partner for identity verification</h2>
          </div>

          <div className="okx-partner-grid">
            {partnerFeatures.map((feat) => (
              <article key={feat.title} className="okx-partner-card">
                <div className="okx-3d-icon-box">
                  <img
                    src={feat.icon}
                    alt={feat.title}
                    className="okx-3d-icon-img"
                    loading="lazy"
                  />
                </div>
                <h3>{feat.title}</h3>
                <p>{feat.description}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ========================================================= */}
        {/* SECTION 3: Build your verification pipeline               */}
        {/* Modeled on OKX Page 1 Section 3 "Build your portfolio"    */}
        {/* ========================================================= */}
        <section id="coverage" className="okx-section okx-portfolio-section content-width">
          <div className="okx-portfolio-layout">
            {/* Left copy */}
            <div className="okx-portfolio-copy">
              <h2>Build your verification pipeline</h2>
              <p>
                Take control of your onboarding funnel. Whether you&apos;re a seasoned compliance team or just starting out, easily verify over 190+ jurisdictions on your terms, with low latency.
              </p>
              <div className="mt-8">
                <Link to="/verify/new" className="okx-pill-btn okx-pill-btn-white">
                  Buy coverage <ArrowRight size={15} />
                </Link>
              </div>
            </div>

            {/* Right: Real-time KYC Ticker Cards */}
            <div className="okx-ticker-grid">
              {tickerCards.map((card) => (
                <div key={card.id} className="okx-ticker-card">
                  <div className="ticker-card-top">
                    <span className="ticker-flag">{card.flag}</span>
                    <span className="ticker-code">{card.code}</span>
                    <span className={`ticker-badge ${card.badgeType === "positive" ? "badge-positive" : "badge-neutral"}`}>
                      {card.badge}
                    </span>
                  </div>
                  <div className="ticker-main">
                    <h3 className="ticker-name">{card.name}</h3>
                    <div className="ticker-stats">
                      <span className="ticker-val">{card.accuracy}</span>
                      <span className="ticker-latency">{card.latency}</span>
                    </div>
                  </div>
                  <div className="ticker-footer">
                    <small>{card.engine}</small>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ========================================================= */}
        {/* SECTION 4: Your gateway to identity verification is just a click away */}
        {/* Modeled on OKX Page 2 Section 4                           */}
        {/* ========================================================= */}
        <section className="okx-section content-width">
          <div className="okx-section-header-center">
            <h2>Your gateway to identity verification is just a click away</h2>
          </div>

          <div className="okx-gateway-grid">
            {gatewayCards.map((card) => (
              <article key={card.title} className="okx-gateway-card">
                <div className="okx-gateway-icon-wrap">
                  <img
                    src={card.icon}
                    alt={card.title}
                    className="okx-gateway-icon-img"
                    loading="lazy"
                  />
                </div>
                <h3>{card.title}</h3>
                <p>{card.description}</p>
                <div className="okx-gateway-btn-wrap">
                  <Link to={card.link} className="okx-pill-btn okx-pill-btn-dark okx-pill-btn-sm">
                    {card.btnText}
                  </Link>
                </div>
              </article>
            ))}
          </div>
        </section>

        {/* ========================================================= */}
        {/* SECTION 5: Inspect every signal with forensic precision   */}
        {/* Modeled on OKX Page 2 Section 5 "Find your trades" Orderbook */}
        {/* ========================================================= */}
        <section id="terminal" className="okx-section content-width">
          <div className="okx-section-header-center">
            <h2>Find your trades &amp; signals</h2>
            <p className="okx-section-sub">
              Gain the edge in fraud prevention. Enjoy low-latency processing, ultra-accurate extractions, and powerful APIs.
            </p>
          </div>

          <div className="okx-terminal-container">
            {/* Terminal Top Bar */}
            <div className="terminal-header-bar">
              <div className="terminal-tab-group">
                <button
                  type="button"
                  className={activeTelemetryTab === "stream" ? "tab-active" : ""}
                  onClick={() => setActiveTelemetryTab("stream")}
                >
                  Live Stream
                </button>
                <button
                  type="button"
                  className={activeTelemetryTab === "ocr" ? "tab-active" : ""}
                  onClick={() => setActiveTelemetryTab("ocr")}
                >
                  OCR Book
                </button>
                <button
                  type="button"
                  className={activeTelemetryTab === "biometrics" ? "tab-active" : ""}
                  onClick={() => setActiveTelemetryTab("biometrics")}
                >
                  Biometric Depth
                </button>
              </div>

              <div className="terminal-meta-status">
                <span className="status-dot animate-pulse" />
                <span>Connected to WebSocket Gateway: <strong className="text-white">wss://api.verix.local/v1/feed</strong></span>
              </div>
            </div>

            {/* Orderbook Table Layout mirroring OKX */}
            <div className="terminal-table-wrapper">
              <table className="okx-orderbook-table">
                <thead>
                  <tr>
                    <th scope="col">Maker</th>
                    <th scope="col">Qty</th>
                    <th scope="col">Bid</th>
                    <th scope="col" className="text-center">Action</th>
                    <th scope="col">Ask</th>
                    <th scope="col">Qty</th>
                    <th scope="col">Maker</th>
                  </tr>
                </thead>
                <tbody>
                  {telemetryRows.map((row, i) => (
                    <tr key={row.applicant}>
                      <td className="cell-maker">
                        <strong>{row.applicant}</strong>
                      </td>
                      <td className="cell-qty">{row.docType}</td>
                      <td className="cell-bid">
                        <span className="bid-badge">{row.bidVal}</span>
                      </td>
                      <td className="cell-action">
                        {row.status === "approved" ? (
                          <div className="order-pill-group">
                            <span className="order-pill order-pill-sell">Sell</span>
                            <span className="order-pill order-pill-buy">Buy {row.askVal}</span>
                          </div>
                        ) : (
                          <div className="order-pill-group">
                            <span className="order-pill order-pill-review">Review</span>
                          </div>
                        )}
                      </td>
                      <td className="cell-ask">
                        <span className="ask-val">{row.biometricScore}</span>
                      </td>
                      <td className="cell-qty-2">{row.latency}</td>
                      <td className="cell-maker-2">{row.reviewer}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Bottom Terminal Controls */}
            <div className="terminal-footer-bar">
              <div className="terminal-stats-row">
                <span>Latency: <strong>14ms</strong></span>
                <span>TPM: <strong>1,240 req/min</strong></span>
                <span>Antispoof Model: <strong>MobileNet-V3 3D BFM ONNX</strong></span>
              </div>
              <Link to="/review" className="terminal-open-btn">
                <span>Open Full Queue</span>
                <ChevronRight size={14} />
              </Link>
            </div>
          </div>
        </section>

        {/* ========================================================= */}
        {/* SECTION 6: Questions? We've got answers. (OKX FAQ)         */}
        {/* Modeled on OKX Page 3 FAQ Accordion                       */}
        {/* ========================================================= */}
        <section id="faq" className="okx-section content-width">
          <div className="okx-faq-heading">
            <h2>Questions? We&apos;ve got answers.</h2>
          </div>

          <div className="okx-faq-accordion">
            {faqList.map((faq, index) => {
              const isOpen = openFaq === index;
              return (
                <div key={faq.question} className={`okx-faq-row ${isOpen ? "open" : ""}`}>
                  <button
                    type="button"
                    className="okx-faq-trigger"
                    aria-expanded={isOpen}
                    onClick={() => setOpenFaq(isOpen ? null : index)}
                  >
                    <span>{faq.question}</span>
                    <span className="okx-faq-icon" aria-hidden="true">
                      {isOpen ? "−" : "+"}
                    </span>
                  </button>
                  {isOpen && (
                    <div className="okx-faq-answer">
                      <p>{faq.answer}</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </main>

      {/* ========================================================= */}
      {/* OKX-STYLE MEGA FOOTER                                     */}
      {/* ========================================================= */}
      <SiteFooter />

      {/* ========================================================= */}
      {/* Mobile Demo / App Download QR Modal                       */}
      {/* ========================================================= */}
      {qrModalOpen && (
        <div className="okx-modal-backdrop" role="dialog" aria-modal="true">
          <div className="okx-modal-card">
            <div className="okx-modal-header">
              <h3>Scan to test on mobile</h3>
              <button
                type="button"
                className="okx-modal-close"
                aria-label="Close modal"
                onClick={() => setQrModalOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <div className="okx-modal-body">
              <div className="modal-qr-frame">
                <img
                  src="/assets/okx/qr_code.png"
                  alt="QR code to test mobile KYC session"
                  className="modal-qr-img"
                />
              </div>
              <p className="modal-qr-instructions">
                Open your smartphone camera or QR scanner. Experience guided Cambodian ID capture and active 3D face liveness in real time.
              </p>
              <div className="modal-actions">
                <Link
                  to="/verify/new"
                  className="okx-pill-btn okx-pill-btn-white w-full"
                  onClick={() => setQrModalOpen(false)}
                >
                  Create Web Session Instead
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
