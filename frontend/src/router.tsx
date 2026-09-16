/**
 * Code-based route tree. Screens are lazy chunks; sub-tabs and ids are typed path params.
 */

import { createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect } from '@tanstack/react-router'
import { Shell } from '@/shell/shell'
import { Empty, ErrorNotice, Page, Panel } from '@/ui/kit'

const root = createRootRoute({ component: Shell })

const index = createRoute({
  getParentRoute: () => root,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/dashboard' })
  },
})

const dashboard = createRoute({
  getParentRoute: () => root,
  path: '/dashboard',
  component: lazyRouteComponent(() => import('@/screens/dashboard'), 'DashboardScreen'),
})

const matrix = createRoute({
  getParentRoute: () => root,
  path: '/matrix',
  component: lazyRouteComponent(() => import('@/screens/matrix'), 'MatrixScreen'),
})

const data = createRoute({ getParentRoute: () => root, path: '/data' })
const dataIndex = createRoute({
  getParentRoute: () => data,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/data/$tab', params: { tab: 'fields' } })
  },
})
const dataTab = createRoute({
  getParentRoute: () => data,
  path: '$tab',
  component: lazyRouteComponent(() => import('@/screens/data'), 'DataScreen'),
})

const labs = createRoute({ getParentRoute: () => root, path: '/labs' })
const labsIndex = createRoute({
  getParentRoute: () => labs,
  path: '/',
  component: lazyRouteComponent(() => import('@/screens/research-labs'), 'ResearchLabsScreen'),
})
const searchLab = createRoute({
  getParentRoute: () => labs,
  path: 'search',
  component: lazyRouteComponent(() => import('@/screens/search-lab'), 'SearchLabScreen'),
})
const templateLab = createRoute({
  getParentRoute: () => labs,
  path: 'template',
  component: lazyRouteComponent(() => import('@/screens/template-lab'), 'TemplateLabScreen'),
})
const evolutionLab = createRoute({
  getParentRoute: () => labs,
  path: 'evolution',
  component: lazyRouteComponent(() => import('@/screens/evolution-lab'), 'EvolutionLabScreen'),
})
const powerPoolLab = createRoute({
  getParentRoute: () => labs,
  path: 'power-pool',
  component: lazyRouteComponent(() => import('@/screens/power-pool-lab'), 'PowerPoolLabScreen'),
})
/** Links to the retired labs land on the lab that replaced them. */
const retiredLab = createRoute({
  getParentRoute: () => labs,
  path: '$labId',
  beforeLoad: () => {
    throw redirect({ to: '/labs/search' })
  },
})

const tasks = createRoute({
  getParentRoute: () => root,
  path: '/tasks',
  component: lazyRouteComponent(() => import('@/screens/tasks'), 'TasksScreen'),
})

const pool = createRoute({ getParentRoute: () => root, path: '/pool' })
const poolIndex = createRoute({
  getParentRoute: () => pool,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/pool/$tab', params: { tab: 'stored' } })
  },
})
const poolTab = createRoute({
  getParentRoute: () => pool,
  path: '$tab',
  component: lazyRouteComponent(() => import('@/screens/pool'), 'PoolScreen'),
})

const ai = createRoute({ getParentRoute: () => root, path: '/ai' })
const aiIndex = createRoute({
  getParentRoute: () => ai,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/ai/$tab', params: { tab: 'providers' } })
  },
})
const aiTab = createRoute({
  getParentRoute: () => ai,
  path: '$tab',
  component: lazyRouteComponent(() => import('@/screens/ai'), 'AiScreen'),
})
const aiThread = createRoute({
  getParentRoute: () => ai,
  path: 'assistant/$threadId',
  component: lazyRouteComponent(() => import('@/screens/ai'), 'AiScreen'),
})

const quantAi = createRoute({
  getParentRoute: () => root,
  path: '/quant-ai',
  component: lazyRouteComponent(() => import('@/screens/quant-ai'), 'QuantAiScreen'),
})

const storeBooth = createRoute({
  getParentRoute: () => root,
  path: '/quant-ai/store-booth-1',
  component: lazyRouteComponent(() => import('@/screens/store-booth-1'), 'StoreBoothScreen'),
})

const pyramids = createRoute({
  getParentRoute: () => root,
  path: '/pyramids',
  component: lazyRouteComponent(() => import('@/screens/pyramids'), 'PyramidsScreen'),
})

const routeTree = root.addChildren([
  index,
  dashboard,
  matrix,
  data.addChildren([dataIndex, dataTab]),
  labs.addChildren([labsIndex, searchLab, templateLab, evolutionLab, powerPoolLab, retiredLab]),
  tasks,
  pool.addChildren([poolIndex, poolTab]),
  ai.addChildren([aiIndex, aiTab, aiThread]),
  quantAi,
  storeBooth,
  pyramids,
])

/** A screen that throws says so instead of going blank (CLAUDE.md anti-goal 3). */
function RouteError({ error }: { error: unknown }) {
  return (
    <Page>
      <ErrorNotice error={error} title="This screen failed to render" />
    </Page>
  )
}

function NotFound() {
  return (
    <Page>
      <Panel>
        <Empty title="There is no screen here">Use the sidebar, or press ⌘K to go to a screen or lab.</Empty>
      </Panel>
    </Page>
  )
}

export const router = createRouter({
  routeTree,
  defaultPreload: 'intent',
  defaultErrorComponent: RouteError,
  defaultNotFoundComponent: NotFound,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
