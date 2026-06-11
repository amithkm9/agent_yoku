import { Navigate, Route, Routes } from "react-router-dom";
import { Login } from "./pages/Login";
import { Chat } from "./pages/Chat";
import { Inbox } from "./pages/Inbox";
import { Settings } from "./pages/Settings";
import { Trends } from "./pages/Trends";
import { getToken } from "./lib/api";

function RequireAuth({ children }: { children: React.ReactNode }) {
  return getToken() ? <>{children}</> : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Chat />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Settings />
          </RequireAuth>
        }
      />
      <Route
        path="/inbox"
        element={
          <RequireAuth>
            <Inbox />
          </RequireAuth>
        }
      />
      <Route
        path="/trends"
        element={
          <RequireAuth>
            <Trends />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
