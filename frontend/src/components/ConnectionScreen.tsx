import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { useConnection } from "../app/ConnectionContext";
import { Button } from "./ui";

export function ConnectionScreen() {
  const { baseUrl: initialUrl, connect } = useConnection();
  const [baseUrl, setBaseUrl] = useState(initialUrl);
  const [apiKey, setApiKey] = useState("");
  const [show, setShow] = useState(false);
  const [rememberForTab, setRemember] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true); setError(""); setNotice("");
    try {
      const health = await connect({ baseUrl, apiKey, rememberForTab });
      if (health.status === "degraded") setNotice("Connected. Backend health is degraded; the model artifact may be unavailable.");
    } catch (caught) {
      const apiError = caught instanceof ApiError ? caught : null;
      setError(apiError?.status === 401 ? "The API key was rejected." : apiError?.message ?? "Unable to connect.");
    } finally {
      setPending(false);
    }
  };

  return <main className="connection-page">
    <section className="connection-intro">
      <div className="brand brand--large"><span className="brand__mark"><ShieldCheck /></span><span><strong>ChargeGuard</strong><small>Dispute intelligence</small></span></div>
      <p className="eyebrow">Secure operator workspace</p><h1>Recover revenue with evidence, not guesswork.</h1>
      <p>Connect directly to your ChargeGuard service. Credentials remain in memory unless you explicitly remember them for this browser tab.</p>
      <ul><li>Authenticated portfolio intelligence</li><li>Auditable case recommendations</li><li>Protected Razorpay operations</li></ul>
    </section>
    <section className="connection-card" aria-labelledby="connect-title">
      <p className="eyebrow">Service access</p><h2 id="connect-title">Connect to ChargeGuard</h2><p>Health is checked first, then an authenticated endpoint verifies the key.</p>
      <form onSubmit={submit}>
        <label>API base URL<input type="url" required value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} autoComplete="url" /></label>
        <div className="field"><label htmlFor="api-key">API key</label><span className="secret-input"><input id="api-key" type={show ? "text" : "password"} required value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" /><button type="button" className="icon-button" onClick={() => setShow(!show)} aria-label={show ? "Hide API key" : "Show API key"}>{show ? <EyeOff /> : <Eye />}</button></span></div>
        <label className="check-row"><input type="checkbox" checked={rememberForTab} onChange={(event) => setRemember(event.target.checked)} />Remember for this tab only</label>
        {error ? <p className="form-error" role="alert">{error}</p> : null}{notice ? <p className="form-notice" role="status">{notice}</p> : null}
        <Button type="submit" loading={pending}>Test and connect</Button>
      </form>
      <small className="security-note">The key is never stored in localStorage or included in the application bundle.</small>
    </section>
  </main>;
}
