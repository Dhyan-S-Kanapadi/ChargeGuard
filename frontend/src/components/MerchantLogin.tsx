import { useState, type FormEvent } from "react";
import { loginClient, merchantApi } from "../app/supabase";
import { useConnection } from "../app/ConnectionContext";
import { Button } from "./ui";

export function MerchantLogin({ url, publishableKey }: { url: string; publishableKey: string }) {
  const { connectSupabase } = useConnection();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [factor, setFactor] = useState("");
  const [qr, setQr] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [recovery, setRecovery] = useState(false);
  const auth = () => loginClient(url, publishableKey);

  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError("");
    try {
      const sdk = auth();
      if (recovery) {
        const result = await sdk.auth.verifyOtp({ email, token: code, type: "recovery" });
        if (result.error) throw result.error;
        const update = await sdk.auth.updateUser({ password });
        if (update.error) throw update.error;
        await sdk.auth.signOut({ scope: "global" });
        setRecovery(false); setPassword(""); setCode("");
        setNotice("Password updated. Sign in again; MFA still applies.");
        return;
      }
      if (factor) {
        const result = await sdk.auth.mfa.challengeAndVerify({ factorId: factor, code });
        if (result.error) throw result.error;
      } else {
        const result = await sdk.auth.signInWithPassword({ email, password });
        if (result.error) throw result.error;
        setPassword("");
        const user = await merchantApi(sdk).currentUser();
        const needsMfa = user.platform_admin || Object.values(user.memberships).some(role => role !== "read_only");
        if (needsMfa && user.aal !== "aal2") {
          const factors = await sdk.auth.mfa.listFactors();
          if (factors.error) throw factors.error;
          const existing = factors.data.totp.find(item => item.status === "verified");
          if (existing) setFactor(existing.id);
          else {
            // Remove only this user's unverified TOTP enrollment left by an interrupted setup.
            for (const item of factors.data.all.filter(item => item.factor_type === "totp" && item.status === "unverified")) {
              const removed = await sdk.auth.mfa.unenroll({ factorId: item.id });
              if (removed.error) throw removed.error;
            }
            const enrolled = await sdk.auth.mfa.enroll({ factorType: "totp", friendlyName: "ChargeGuard" });
            if (enrolled.error) throw enrolled.error;
            setFactor(enrolled.data.id); setQr(enrolled.data.totp.qr_code);
          }
          return;
        }
      }
      await connectSupabase(sdk);
    } catch {
      setError("Sign-in could not complete. Check credentials, code and account membership, then retry. Contact your administrator if it persists.");
    } finally { setPending(false); }
  }

  return <form onSubmit={submit}>
    <p>Merchant login · Supabase Auth. Sessions stay in memory and end on refresh.</p>
    {!factor && <>
      <label>Email<input type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>{recovery ? "New password" : "Password"}<input type="password" autoComplete={recovery ? "new-password" : "current-password"} required value={password} onChange={e => setPassword(e.target.value)} /></label>
    </>}
    {qr && <><p>Scan this code with your authenticator app, then enter its six-digit code.</p><img src={qr} alt="Authenticator enrollment QR code" width="200" height="200" /></>}
    {(factor || recovery) && <label>{recovery ? "Email recovery code" : "Authenticator code"}<input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={e => setCode(e.target.value)} /></label>}
    {error && <p role="alert" className="form-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <Button type="submit" loading={pending}>{recovery ? "Reset password" : factor ? "Verify MFA and connect" : "Sign in"}</Button>
    {!factor && !recovery && <Button type="button" disabled={pending || !email} onClick={async () => {
      setPending(true); setError("");
      try {
        const result = await auth().auth.resetPasswordForEmail(email);
        if (result.error) throw result.error;
        setRecovery(true); setPassword("");
        setNotice("If the account exists, a recovery email will arrive. Enter its code and a new password.");
      } catch { setError("Recovery is unavailable. Contact your administrator."); }
      finally { setPending(false); }
    }}>Forgot password</Button>}
  </form>;
}
