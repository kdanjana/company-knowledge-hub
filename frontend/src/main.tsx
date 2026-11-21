import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { AuthProvider, HomeRedirect, RequireAuth } from "./auth";
import Layout from "./components/Layout";
import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import EvaluationPage from "./pages/EvaluationPage";
import FindingDetailPage from "./pages/FindingDetailPage";
import FindingsPage from "./pages/FindingsPage";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import UploadPage from "./pages/UploadPage";

const auditorOnly = (page: React.ReactNode) => <RequireAuth role="auditor">{page}</RequireAuth>;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<HomeRedirect />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="upload" element={<UploadPage />} />
            <Route path="findings" element={auditorOnly(<FindingsPage />)} />
            <Route path="findings/:id" element={auditorOnly(<FindingDetailPage />)} />
            <Route path="runs" element={auditorOnly(<RunsPage />)} />
            <Route path="documents" element={auditorOnly(<DocumentsPage />)} />
            <Route path="evaluation" element={auditorOnly(<EvaluationPage />)} />
            <Route path="*" element={<HomeRedirect />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
