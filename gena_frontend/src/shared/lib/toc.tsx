import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export interface TocItem {
  id: string;
  label: string;
}

interface TocContextValue {
  sections: TocItem[];
  setSections: (items: TocItem[]) => void;
}

const TocContext = createContext<TocContextValue>({ sections: [], setSections: () => {} });

export function TocProvider({ children }: { children: ReactNode }) {
  const [sections, setSections] = useState<TocItem[]>([]);
  return <TocContext.Provider value={{ sections, setSections }}>{children}</TocContext.Provider>;
}

/** Read the section list published by the currently mounted page. */
export function useTocSections(): TocItem[] {
  return useContext(TocContext).sections;
}

/**
 * Publish a page's section list to the left menu. The list is cleared when the
 * page unmounts, so only the active page's sections are ever shown.
 */
export function usePageToc(items: TocItem[]) {
  const { setSections } = useContext(TocContext);
  const key = items.map((i) => `${i.id}:${i.label}`).join('|');
  useEffect(() => {
    setSections(items);
    return () => setSections([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setSections]);
}
