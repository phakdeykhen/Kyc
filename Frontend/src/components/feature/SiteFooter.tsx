import { Link } from "react-router-dom";
import { ChevronDown, Globe, LockKeyhole, QrCode } from "lucide-react";
import Brand from "./Brand";

export default function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="content-width">
        <div className="footer-mega-grid">
          {/* Brand & Language Column */}
          <div className="footer-brand-col">
            <Link to="/" aria-label="Verix KYC home">
              <Brand />
            </Link>
            <p className="footer-copyright">©2017 - 2026 VERIX.COM</p>
            <div className="footer-lang-pill">
              <Globe size={14} />
              <span>English/USD</span>
              <ChevronDown size={12} />
            </div>
          </div>

          {/* Column 1: More about Verix */}
          <div className="footer-col">
            <h4>More about Verix</h4>
            <ul>
              <li><Link to="/#platform">About us</Link></li>
              <li><Link to="/#coverage">Candidate Privacy Notice</Link></li>
              <li><Link to="/#platform">Enterprise Security</Link></li>
              <li><Link to="/developers">API Status</Link></li>
              <li><Link to="/#faq">Terms of Service</Link></li>
              <li><Link to="/#faq">Privacy Policy</Link></li>
              <li><Link to="/#faq">Regulatory Disclosures</Link></li>
            </ul>
          </div>

          {/* Column 2: Products */}
          <div className="footer-col">
            <h4>Products</h4>
            <ul>
              <li><Link to="/verify/new">Document OCR</Link></li>
              <li><Link to="/verify/new">3D Face Liveness</Link></li>
              <li><Link to="/verify/new">Government ID Lookup</Link></li>
              <li><Link to="/review">Manual Review Queue</Link></li>
              <li><Link to="/console">Applicant Console</Link></li>
              <li><Link to="/developers">Developer API</Link></li>
              <li><Link to="/#terminal">Forensic Telemetry</Link></li>
            </ul>
          </div>

          {/* Column 3: Services & Support */}
          <div className="footer-col">
            <h4>Services</h4>
            <ul>
              <li><Link to="/developers">REST &amp; Webhooks</Link></li>
              <li><Link to="/developers">Python SDK</Link></li>
              <li><Link to="/developers">TypeScript Client</Link></li>
              <li><Link to="/#platform">Audit Logs</Link></li>
            </ul>

            <h4 className="mt-6">Support</h4>
            <ul>
              <li><Link to="/#faq">Support Center</Link></li>
              <li><Link to="/developers">Developer Hub</Link></li>
              <li><Link to="/#faq">System Architecture</Link></li>
            </ul>
          </div>

          {/* Column 4: Supported Documents */}
          <div className="footer-col">
            <h4>Coverage</h4>
            <ul>
              <li><Link to="/verify/new">Cambodia National ID</Link></li>
              <li><Link to="/verify/new">Cambodia Passport</Link></li>
              <li><Link to="/verify/new">NSSF Member Card</Link></li>
              <li><Link to="/verify/new">ICAO e-Passport</Link></li>
              <li><Link to="/verify/new">Driver&apos;s License</Link></li>
              <li><Link to="/verify/new">Residence Permit</Link></li>
            </ul>
          </div>

          {/* Column 5: Verify on the go (OKX Download App column) */}
          <div className="footer-col footer-qr-col">
            <h4>Verify on the go with Verix</h4>
            <Link to="/verify/new" className="footer-pill-btn">
              Register
            </Link>
            
            <div className="footer-qr-wrapper">
              <img
                src="/assets/okx/qr_code.png"
                alt="Scan to verify with Verix mobile"
                className="footer-qr-img"
                loading="lazy"
              />
              <span className="footer-qr-caption">Scan to test on mobile</span>
            </div>
          </div>
        </div>

        {/* Bottom Bar: Community & Social Links */}
        <div className="footer-community-bar">
          <div className="footer-community-left">
            <span className="community-title">Community</span>
            <div className="community-icons">
              {/* X / Twitter */}
              <a href="https://x.com" target="_blank" rel="noopener noreferrer" aria-label="X (Twitter)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>
                </svg>
              </a>
              {/* GitHub */}
              <a href="https://github.com" target="_blank" rel="noopener noreferrer" aria-label="GitHub">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/>
                </svg>
              </a>
              {/* Telegram */}
              <a href="https://telegram.org" target="_blank" rel="noopener noreferrer" aria-label="Telegram">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.52 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .37z"/>
                </svg>
              </a>
              {/* Discord */}
              <a href="https://discord.com" target="_blank" rel="noopener noreferrer" aria-label="Discord">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M20.317 4.37a19.791 19.791 0 00-4.885-1.515.074.074 0 00-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 00-5.487 0 12.64 12.64 0 00-.617-1.25.077.077 0 00-.079-.037A19.736 19.736 0 003.677 4.37a.07.07 0 00-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 00.031.057 19.9 19.9 0 005.993 3.03.078.078 0 00.084-.028c.462-.63.874-1.295 1.226-1.994.021-.041.001-.09-.041-.106a13.107 13.107 0 01-1.872-.892.077.077 0 01-.008-.128 10.2 10.2 0 00.372-.292.074.074 0 01.077-.01c3.929 1.793 8.18 1.793 12.061 0a.074.074 0 01.078.01c.12.098.246.198.373.292a.077.077 0 01-.006.127 12.299 12.299 0 01-1.873.893.077.077 0 00-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 00.084.028 19.839 19.839 0 006.002-3.03.077.077 0 00.032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 00-.031-.028zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"/>
                </svg>
              </a>
              {/* LinkedIn */}
              <a href="https://linkedin.com" target="_blank" rel="noopener noreferrer" aria-label="LinkedIn">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.28 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.75M6.88 8.56a1.68 1.68 0 0 0 1.68-1.68c0-.93-.75-1.69-1.68-1.69a1.69 1.69 0 0 0-1.69 1.69c0 .93.76 1.68 1.69 1.68m1.39 9.94v-8.37H5.5v8.37h2.77z"/>
                </svg>
              </a>
            </div>
          </div>

          <div className="footer-community-right">
            <span><LockKeyhole size={12} /> SOC 2 Type II Certified · 256-Bit Encrypted · Proof of Privacy</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
