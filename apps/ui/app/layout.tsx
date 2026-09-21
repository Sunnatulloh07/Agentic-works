export const metadata = { title: "Agent Platform — Operator", description: "Tasdiq navbati va statistika" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="uz">
      <body>{children}</body>
    </html>
  );
}
