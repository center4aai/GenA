import { Suspense, lazy } from 'react';
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom';
import { TocProvider } from '@/shared/lib/toc';
import { AUTH_ENABLED } from '@/shared/config/auth';
import { LoadingState } from '@/shared/ui/StatusBanner';
import { RouteErrorBoundary } from '@/shared/ui/RouteErrorBoundary';
import { Layout } from './layout';
import { HomePage } from '@/features/home/HomePage';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireAuth } from '@/features/auth/RequireAuth';

// Split per route: everything used to land in one ~1.9 MB entry chunk, so the
// login screen pulled in ExcelJS and Recharts before the user clicked anything.
const DocsPage = lazy(() =>
  import('@/features/docs/DocsPage').then((m) => ({ default: m.DocsPage })),
);
const PreprocessingPage = lazy(() =>
  import('@/features/preprocessing/PreprocessingPage').then((m) => ({
    default: m.PreprocessingPage,
  })),
);
const DatasetEditorPage = lazy(() =>
  import('@/features/dataset-editor/DatasetEditorPage').then((m) => ({
    default: m.DatasetEditorPage,
  })),
);
const QueueManagerPage = lazy(() =>
  import('@/features/queue-manager/QueueManagerPage').then((m) => ({
    default: m.QueueManagerPage,
  })),
);
const QueueOverviewPage = lazy(() =>
  import('@/features/queue-manager/QueueOverviewPage').then((m) => ({
    default: m.QueueOverviewPage,
  })),
);
const StatisticsPage = lazy(() =>
  import('@/features/statistics/StatisticsPage').then((m) => ({
    default: m.StatisticsPage,
  })),
);
const DynamicImplementationPage = lazy(() =>
  import('@/features/dynamic-implementation/DynamicImplementationPage').then((m) => ({
    default: m.DynamicImplementationPage,
  })),
);

function lazyRoute(node: React.ReactNode) {
  return <Suspense fallback={<LoadingState />}>{node}</Suspense>;
}

const dataRoutes = [
  { path: 'data_preprocessing', element: lazyRoute(<PreprocessingPage />) },
  { path: 'dataset_editor', element: lazyRoute(<DatasetEditorPage />) },
  { path: 'queue_manager', element: lazyRoute(<QueueManagerPage />) },
  { path: 'queue_overview', element: lazyRoute(<QueueOverviewPage />) },
  { path: 'statistics', element: lazyRoute(<StatisticsPage />) },
  { path: 'dynamic_implementation', element: lazyRoute(<DynamicImplementationPage />) },
];

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'docs', element: lazyRoute(<DocsPage />) },
      ...(AUTH_ENABLED
        ? [
            { path: 'login', element: <LoginPage /> },
            {
              element: <RequireAuth />,
              children: dataRoutes,
            },
          ]
        : dataRoutes),
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
