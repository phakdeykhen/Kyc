export default function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="verix-brand">
      <span className="brand-okx-glyph" aria-hidden="true">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
          <rect x="2" y="2" width="5.5" height="5.5" rx="1.2" fill="#ffffff" />
          <rect x="16.5" y="2" width="5.5" height="5.5" rx="1.2" fill="#ffffff" />
          <rect x="9.25" y="9.25" width="5.5" height="5.5" rx="1.2" fill="#ffffff" />
          <rect x="2" y="16.5" width="5.5" height="5.5" rx="1.2" fill="#ffffff" />
          <rect x="16.5" y="16.5" width="5.5" height="5.5" rx="1.2" fill="#ffffff" />
        </svg>
      </span>
      <span className="brand-wordmark">VERIX<span className="brand-period">.</span></span>
      {!compact && <span className="brand-tag">KYC</span>}
    </span>
  );
}
