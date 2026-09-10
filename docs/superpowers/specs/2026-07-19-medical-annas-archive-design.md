# Medical Specialization & Anna's Archive Integration Design

## 1. Overview
Dự án `paper-search-mcp` sẽ được chuyển đổi (pivot) từ một công cụ tìm kiếm học thuật chung thành một MCP Server chuyên biệt cho lĩnh vực Y sinh (Medical Research). Đồng thời, chuỗi dự phòng tải xuống (Fallback Chain) sẽ được nâng cấp với khả năng tích hợp Anna's Archive để tối đa hóa tỷ lệ tải thành công các tài liệu PDF.

## 2. Lọc Nguồn Dữ Liệu Y Khoa (Medical Specialization)
*   **Mục tiêu**: Giảm thiểu nhiễu thông tin, ưu tiên các báo cáo lâm sàng và nghiên cứu y sinh.
*   **Chi tiết triển khai**: 
    *   Tệp mục tiêu: `paper_search_mcp/server.py`
    *   Sửa đổi biến `ALL_SOURCES` để loại bỏ các nguồn không liên quan (ví dụ: `arxiv`, `iacr`, `dblp`, v.v.).
    *   Chỉ giữ lại các nguồn y sinh cốt lõi: `["pubmed", "pmc", "europepmc", "medrxiv", "biorxiv"]`.
    *   (Tùy chọn tương lai) Cập nhật các câu truy vấn mẫu (prompt) trong công cụ `search_papers` để gợi ý việc sử dụng thuật ngữ MeSH.

## 3. Trình thu thập Anna's Archive (Fallback Chain)
*   **Mục tiêu**: Vượt qua các rào cản truy cập bài báo trả phí bằng cách sử dụng nền tảng Anna's Archive.
*   **Chi tiết triển khai**:
    *   Tạo tệp `paper_search_mcp/academic_platforms/annas_archive.py`.
    *   Thiết kế class `AnnasArchiveFetcher` tuân thủ interface tương tự các Fetcher hiện có.
    *   **Data Flow**:
        1.  Nhận tham số đầu vào là `doi`.
        2.  Thực hiện GET request tới `https://annas-archive.org/search?q=<doi>` với HTTP headers giả lập trình duyệt (User-Agent, Accept, v.v.).
        3.  Dùng `BeautifulSoup` tìm kiếm liên kết chứa trang chi tiết (MD5 hash page).
        4.  Gửi request đến trang chi tiết để tìm các external download links (ví dụ: Libgen.li, Sci-Hub, IPFS).
        5.  Thử tải file PDF từ danh sách links thu thập được cho đến khi thành công hoặc cạn kiệt.
    *   Sửa đổi tệp `server.py` tại hàm `download_with_fallback`: gọi `AnnasArchiveFetcher` nếu `_try_repository_fallback` thất bại, trước khi sử dụng `SciHubFetcher`.

## 4. Tích hợp Antigravity IDE (Môi trường Phát triển)
*   **Mục tiêu**: Cho phép Antigravity IDE sử dụng MCP server trực tiếp từ mã nguồn đang phát triển thay vì gói cài đặt toàn cục.
*   **Chi tiết triển khai**:
    *   Khai báo cấu hình máy chủ MCP vào tệp `D:\Dev\.agents\mcp_config.json`.
    *   Cấu hình mẫu:
        ```json
        {
          "mcpServers": {
            "paper-search-mcp-dev": {
              "command": "uv",
              "args": [
                "run",
                "--directory", "D:/Dev/paper-search-mcp",
                "-m", "paper_search_mcp.server"
              ]
            }
          }
        }
        ```
    *   Điều này giúp mọi thay đổi về code trong Anna's Archive connector sẽ có tác dụng ngay khi khởi động lại Agent.

## 5. Kế hoạch Kiểm thử (Verification Plan)
*   Viết test case trong thư mục `tests/` để đảm bảo `AnnasArchiveFetcher` có thể lấy được liên kết tải xuống từ trang kết quả hợp lệ.
*   Chạy công cụ `download_with_fallback` qua CLI `paper-search` để kiểm chứng luồng hoạt động từ OA -> Anna's Archive -> Sci-Hub.
