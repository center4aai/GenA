import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';
import { TocProvider } from '@/shared/lib/toc';
import { Layout } from './layout';
import { HomePage } from '@/features/home/HomePage';
import { DocsPage } from '@/features/docs/DocsPage';
// Authentication temporarily hidden — see features/auth/authStore.tsx.
// import { LoginPage } from '@/features/auth/LoginPage';
// import { RequireAuth } from '@/features/auth/RequireAuth';
import { PreprocessingPage } from '@/features/preprocessing/PreprocessingPage';
import { DatasetEditorPage } from '@/features/dataset-editor/DatasetEditorPage';
import { QueueManagerPage } from '@/features/queue-manager/QueueManagerPage';
import { StatisticsPage } from '@/features/statistics/StatisticsPage';
import { DynamicImplementationPage } from '@/features/dynamic-implementation/DynamicImplementationPage';

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'docs', element: <DocsPage /> },
      // Authentication temporarily hidden: data pages are public and the
      // /login route + <RequireAuth> gate are disabled. To restore, wrap the
      // pages below in `{ element: <RequireAuth />, children: [...] }` again
      // and re-add `{ path: 'login', element: <LoginPage /> }`.
      // { path: 'login', element: <LoginPage /> },
      { path: 'data_preprocessing', element: <PreprocessingPage /> },
      { path: 'dataset_editor', element: <DatasetEditorPage /> },
      { path: 'queue_manager', element: <QueueManagerPage /> },
      { path: 'statistics', element: <StatisticsPage /> },
      { path: 'dynamic_implementation', element: <DynamicImplementationPage /> },
      // Catch-all: redirect any unknown/stale path (e.g. the removed /login,
      // or bookmarked URLs) to Home instead of showing a raw 404.
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

export function AppRouter() {
  return (
    <TocProvider>
      <RouterProvider router={router} />
    </TocProvider>
  );
}
