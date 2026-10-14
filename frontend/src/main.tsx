import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AsOfControls, AsOfProvider } from "./components/ui";
import DrugDetail from "./pages/DrugDetail";
import Drugs from "./pages/Drugs";
import Eval from "./pages/Eval";
import PatentDetail from "./pages/PatentDetail";
import Patents from "./pages/Patents";
import Review from "./pages/Review";
import "./styles.css";

const Matching = lazy(() => import("./pages/Matching"));

function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">💊 Drug Patent Landscape</div>
        <nav className="nav">
          <NavLink to="/drugs">ยา</NavLink>
          <NavLink to="/patents">สิทธิบัตร</NavLink>
          <NavLink to="/matching">การจับคู่</NavLink>
          <NavLink to="/review">ตรวจทาน</NavLink>
          <NavLink to="/eval">ประเมินผล</NavLink>
        </nav>
        <AsOfControls />
      </header>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/drugs" replace />} />
          <Route path="/drugs" element={<Drugs />} />
          <Route path="/drugs/:id" element={<DrugDetail />} />
          <Route path="/patents" element={<Patents />} />
          <Route path="/patents/:appno" element={<PatentDetail />} />
          <Route path="/matching" element={<Suspense fallback={<div className="empty">กำลังโหลด…</div>}><Matching /></Suspense>} />
          <Route path="/review" element={<Review />} />
          <Route path="/eval" element={<Eval />} />
        </Routes>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><AsOfProvider><App /></AsOfProvider></BrowserRouter>
  </React.StrictMode>,
);
