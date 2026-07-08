import { Routes, Route, Link } from "react-router-dom";
import UploadPage from "./pages/UploadPage";
import BookPage from "./pages/BookPage";
import ReaderPage from "./pages/ReaderPage";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
          <Link to="/" className="text-xl font-bold text-blue-600 hover:text-blue-700">
            📖 AI 双语阅读器
          </Link>
          <nav className="ml-auto text-sm text-gray-500">
            <Link to="/" className="hover:text-blue-600">首页</Link>
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-6">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/books/:bookId" element={<BookPage />} />
          <Route path="/read/:chapterId" element={<ReaderPage />} />
        </Routes>
      </main>
    </div>
  );
}
