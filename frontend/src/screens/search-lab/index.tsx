/**
 * Search Lab: choose datasets, cores and simulations, then add the search to Tasks, where it
 * runs. It writes one- and two-operator Alphas from the datasets' fields, steering towards
 * the best Sharpe.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { PlusIcon } from 'lucide-react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { searchLab, type SearchLabRequest } from '@/api/search-lab'
import { useDebounced } from '@/screens/data/state'
import { labBody, MAX_SIMULATIONS, simulationsValid, useLabMarket, vectorOperatorsOf } from '@/screens/research-labs/lab-task'
import { DatasetsPanel, SettingsPanel } from '@/screens/research-labs/task-settings'
import { Button, ErrorNotice, Page, PageHeader } from '@/ui/kit'
import { useSearchLab } from './state'

export function SearchLabScreen() {
  const draft = useSearchLab()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { chosen, names, choose } = useLabMarket(draft, draft.set, '/labs/search')

  const options = useQuery({ queryKey: ['search-lab', 'options'], queryFn: searchLab.options, staleTime: 5 * 60_000 })
  const vectorOperators = vectorOperatorsOf(draft, options.data?.vector)
  const body: SearchLabRequest = labBody(draft, vectorOperators)
  const key = JSON.stringify(body)
  const settledKey = useDebounced(key, 300)
  const preview = useQuery({
    queryKey: ['search-lab', 'preview', settledKey],
    queryFn: () => searchLab.preview(JSON.parse(settledKey) as SearchLabRequest),
    enabled: chosen && options.isSuccess && settledKey === key,
    placeholderData: keepPreviousData,
  })
  const plan = chosen ? preview.data : undefined

  const maxSimulations = options.data?.maxSimulations ?? MAX_SIMULATIONS
  const add = useMutation({
    mutationFn: (count: number) => searchLab.addTask({ ...body, simulations: count }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lab-tasks'] })
      toast.success('Task added', { action: { label: 'Open Tasks', onClick: () => void navigate({ to: '/tasks' }) } })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const ready = plan !== undefined && settledKey === key && !preview.isFetching && plan.problems.length === 0 && simulationsValid(draft, maxSimulations)

  return (
    <Page>
      <PageHeader
        title="Search Lab"
        actions={
          <Button variant="primary" disabled={!ready} loading={add.isPending} onClick={() => draft.simulations !== null && add.mutate(draft.simulations)}>
            <PlusIcon />
            Add Task
          </Button>
        }
      />
      {options.isError && <ErrorNotice error={options.error} title="Could not read your operators" />}
      <DatasetsPanel ids={draft.datasetIds} names={names} onChoose={choose} onRemove={(id) => draft.set({ datasetIds: draft.datasetIds.filter((x) => x !== id) })} />
      <SettingsPanel
        draft={draft}
        set={draft.set}
        vector={options.data?.vector ?? []}
        chosenVector={vectorOperators}
        decays={options.data?.decays}
        maxSimulations={maxSimulations}
        plan={plan}
        error={chosen && preview.isError ? preview.error : null}
      />
    </Page>
  )
}
