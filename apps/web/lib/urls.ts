// LinkedIn is never fetched, scraped or automated (SPEC §11.2, ATS-04). This check runs
// in the browser before a request is built and again on the server.

export function isLinkedInUrl(raw: string): boolean {
  const value = raw.trim();
  try {
    const host = new URL(/^[a-z]+:\/\//i.test(value) ? value : `https://${value}`).hostname.toLowerCase();
    return host === "linkedin.com" || host.endsWith(".linkedin.com") || host === "lnkd.in";
  } catch {
    return /linkedin\.com|lnkd\.in/i.test(value);
  }
}
