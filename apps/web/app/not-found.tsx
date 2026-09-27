import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-2 text-center">
      <div className="font-mono text-xs text-fg-subtle">404</div>
      <h1 className="text-lg font-semibold">This page does not exist</h1>
      <Link href="/" className="text-sm text-accent hover:underline">
        Back to AnalystOS
      </Link>
    </div>
  );
}
