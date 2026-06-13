import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sorteio de Comentários Instagram",
  description: "Sorteio de comentários para o Instagram com arquitetura Serverless.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
