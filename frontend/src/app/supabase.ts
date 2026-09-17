import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { ApiClient } from "../api/client";

let client: SupabaseClient | null = null;
export function loginClient(url: string, publishableKey: string) {
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url) || !publishableKey.startsWith("sb_publishable_")) {
    throw new Error("Invalid public identity configuration.");
  }
  client ??= createClient(url, publishableKey, { auth: {
    persistSession: false, autoRefreshToken: true, detectSessionInUrl: false,
  } });
  return client;
}

export function merchantApi(auth: SupabaseClient) {
  return new ApiClient(window.location.origin, "", 12_000, "", async () => {
    const { data, error } = await auth.auth.getSession();
    if (error || !data.session) throw new Error("Sign in again.");
    return data.session.access_token;
  });
}
