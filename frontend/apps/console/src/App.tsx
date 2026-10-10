import { useEffect, useState } from "react";

type Status = {
  name: string;
  version: string;
  stage: "foundation";
  integrations: Array<{
    key: string;
    name: string;
    description: string;
    state: "planned";
  }>;
};

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    setStatus(null);
    async function load() {
      try {
        const response = await fetch("/api/v1/status", { signal: controller.signal });
        if (!response.ok) throw new Error(`Service returned HTTP ${response.status}.`);
        const data: Status = await response.json();
        if (!controller.signal.aborted) setStatus(data);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Unable to reach the service.");
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [attempt]);

  return (
    <div className="shell">
      <header>
        <a className="brand" href="/" aria-label="Channel Gateway home">
          <span className="brand-mark" aria-hidden="true">
            ↔
          </span>
          Channel Gateway
        </a>
        <a className="repository" href="https://github.com/saurlax/channel-gateway">
          GitHub ↗
        </a>
      </header>
      <main>
        <section className="intro" aria-labelledby="title">
          <span className="eyebrow">COMMUNICATION FOR AGENTS</span>
          <h1 id="title">A port into every conversation.</h1>
          <p className="lead">
            Connect your agents to the places people communicate. Route incoming events, then let
            agents read and act across channels.
          </p>
          <div className="service-state" role="status" aria-live="polite">
            <span className={`indicator ${status ? "online" : ""}`} />
            {status
              ? `Service online · v${status.version}`
              : error
                ? "Service unavailable"
                : "Connecting to service…"}
          </div>
        </section>
        {error && (
          <div className="error" role="alert">
            <div>
              <strong>Could not load service status.</strong>
              <p>{error} Check that the backend is running.</p>
            </div>
            <button onClick={() => setAttempt((value) => value + 1)}>Retry</button>
          </div>
        )}
        <section className="foundation" aria-labelledby="foundation-title">
          <div>
            <span className="eyebrow">PROJECT STATUS</span>
            <h2 id="foundation-title">The foundation is in place.</h2>
            <p>
              This first version provides the service, API, and console skeleton. Channel
              connections, routing, and message history will arrive as integrations are implemented.
            </p>
          </div>
          <a className="api-link" href="/api/docs">
            Explore the API ↗
          </a>
        </section>
        <section className="integrations" aria-labelledby="integrations-title">
          <div className="section-heading">
            <h2 id="integrations-title">Integration direction</h2>
            <span>Implementation has not started</span>
          </div>
          <div className="cards">
            {status?.integrations.map((integration, index) => (
              <article key={integration.key}>
                <div className="card-top">
                  <span className="card-number">0{index + 1}</span>
                  <span className="badge">Planned</span>
                </div>
                <h3>{integration.name}</h3>
                <p>{integration.description}</p>
              </article>
            ))}
            {!status && !error && <p role="status">Loading integration status…</p>}
          </div>
        </section>
      </main>
      <footer>
        <span>Any channel. Any agent.</span>
        <span>Independent · Open source · Apache-2.0</span>
      </footer>
    </div>
  );
}
