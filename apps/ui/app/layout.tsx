import './globals.css';

export const metadata = { title: "Agent Platform", description: "Do‘kon suhbatlari, tasdiqlar va buyurtmalar" };
export const viewport = {
  width: 'device-width', initialScale: 1, viewportFit: 'cover',
  themeColor: [{media: '(prefers-color-scheme: light)', color: '#ffffff'}, {media: '(prefers-color-scheme: dark)', color: '#19232b'}],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="uz">
      <body>{children}</body>
    </html>
  );
}
