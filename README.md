# EduBridge - Windows desktop prototype

This is the first UI milestone for EduBridge, targeting Windows laptops. It contains a starter resource library, nearby sharing screen, and profile screen. Sample resources are demo data; account management, persistent database storage, peer discovery, and file transfer are not connected yet.

## Prepare a Windows build

On a Windows development computer:

1. Install Flutter and Visual Studio with the **Desktop development with C++** workload.
2. Open PowerShell in this folder and run `flutter create --platforms=windows .`.
3. Run `flutter pub get`.
4. Start the app with `flutter run -d windows`.

Flutter's Windows desktop build requires Windows development tools. The current workspace does not have Flutter installed, so the Windows runner files and executable have not been generated here.

## Windows-first build sequence

1. App shell and screen layout (this milestone).
2. Student/teacher profiles and local SQLite database.
3. Peer discovery and connections between computers on the same local Wi-Fi network.
4. File transfer with progress, retry, and resume.
5. Instructor signatures, resource indexing, and tests on multiple Windows laptops.

For the first computer prototype, use the same local Wi-Fi network for discovery and transfer. This is simpler to demonstrate and troubleshoot than direct Wi-Fi pairing. The application should show the peer's name and ask the user to approve a transfer before accepting a file. Add direct Wi-Fi networking only if the project specifically needs computers to connect without a shared router.
