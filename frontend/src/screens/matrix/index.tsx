import { Page, PageHeader } from '@/ui/kit'
import { SimulationMatrix } from './matrix'

export function MatrixScreen() {
  return (
    <Page>
      <PageHeader title="Simulation Matrix" description="What each core is running right now." />
      <SimulationMatrix />
    </Page>
  )
}
