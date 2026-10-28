import type { SignalIdentity } from "@/lib/sdr/signal-id/types";

type SignalIdentityHintProps = {
  identity: SignalIdentity;
};

/**
 * Named SigID match under a scan row. The external link is a nested control so
 * opening the wiki does not also tune the receiver.
 */
export function SignalIdentityHint({ identity }: SignalIdentityHintProps) {
  const scorePct = Math.round(identity.score * 100);

  return (
    <span className="signal-identity">
      <span className="signal-identity-name" title={identity.description}>
        {identity.name}
      </span>
      <span className="signal-identity-meta">
        <span
          className="signal-identity-origin"
          title={
            identity.origin === "artemis"
              ? "From Artemis-DB (sigidwiki.com content, GPL-3)"
              : "Hand-curated SigSense catalog entry"
          }
        >
          {identity.origin === "artemis" ? "Artemis" : "Curated"}
        </span>
        <span className="tabular-nums">{scorePct}%</span>
        <a
          href={identity.url}
          target="_blank"
          rel="noopener noreferrer"
          className="signal-identity-link"
          onClick={(event) => event.stopPropagation()}
          onPointerDown={(event) => event.stopPropagation()}
          aria-label={`Open ${identity.name} on Signal Identification Wiki`}
        >
          SigID
        </a>
      </span>
    </span>
  );
}
