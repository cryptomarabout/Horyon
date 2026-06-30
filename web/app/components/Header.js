import Link from "next/link";
import ThemeToggle from "./ThemeToggle";
import NavSearch from "./NavSearch";
import MainNav from "./MainNav";
export default function Header() {
  return (
    <header className="header">
      <Link href="/" prefetch={false} className="brand" aria-label="Horyon home">
        <div className="brand-icon" aria-hidden>
          <img src="/falcon.png" alt="" />
        </div>
        <div className="brand-text">
          <span className="brand-name">HORYON</span>
          <span className="brand-sub">Crypto Market Intelligence</span>
        </div>
      </Link>

      <MainNav />

      <div className="nav-search-center">
        <NavSearch />
      </div>

      <div className="header-right">
        <ThemeToggle />
      </div>
    </header>
  );
}
