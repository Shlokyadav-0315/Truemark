import { Bricolage_Grotesque, Newsreader } from "next/font/google";
import "./globals.css";

const ui = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-ui", display: "swap" });
const copy = Newsreader({ subsets: ["latin"], variable: "--font-copy", display: "swap" });

export const metadata = {
  title: "TrueMark: check copy before it goes out",
  description: "Check marketing copy against brand rules and approved facts, with a source for every finding.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${ui.variable} ${copy.variable}`}>
      <body>{children}</body>
    </html>
  );
}
