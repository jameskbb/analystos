"use client";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: 32, fontSize: 13 }}>
        <h1 style={{ fontSize: 16, fontWeight: 600 }}>AnalystOS hit an unexpected error</h1>
        <pre style={{ whiteSpace: "pre-wrap", color: "#b4372f" }}>{error.message}</pre>
        <button type="button" onClick={reset} style={{ marginTop: 12 }}>
          Try again
        </button>
      </body>
    </html>
  );
}
