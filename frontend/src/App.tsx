import { Routes, Route, Link, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import UploadPage from "./pages/UploadPage";
import BookPage from "./pages/BookPage";
import ReaderPage from "./pages/ReaderPage";
import LoginPage from "./pages/LoginPage";

function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="bg-white border-b shadow-sm">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
        <Link to="/" className="text-xl font-bold text-blue-600 hover:text-blue-700 shrink-0">
          📖 PageBridge
        </Link>
        <nav className="ml-auto flex items-center gap-4 text-sm">
          {user ? (
            <>
              <span className="text-gray-500">{user.email}</span>
              <button
                onClick={async () => { await logout(); navigate("/"); }}
                className="text-gray-400 hover:text-red-500"
              >
                退出
              </button>
            </>
          ) : (
            <Link to="/login" className="text-blue-600 hover:text-blue-800">
              登录 / 注册
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}

function AppRoutes() {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <Header />
      <main className="flex-1">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={
            <div className="max-w-6xl mx-auto w-full px-4 py-6"><UploadPage /></div>
          } />
          <Route path="/books/:bookId" element={
            <div className="max-w-6xl mx-auto w-full px-4 py-6"><BookPage /></div>
          } />
          <Route path="/read/:bookId" element={<ReaderPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
