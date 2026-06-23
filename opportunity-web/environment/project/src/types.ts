export type Confidence = 'High' | 'Medium' | 'Low';

export type Status = 'New' | 'Contacted' | 'Won' | 'Lost';

/** An upsell/growth opportunity for an existing ads customer (the data feed). */
export type Opportunity = {
  id: string;
  customerName: string;
  industry: string;
  product: string;
  currentSpendMonthly: number;
  estUpliftMonthly: number;
  confidence: Confidence;
  rationale: string;
};

/** Rep-edited sales workflow state, persisted separately from the feed. */
export type OppState = {
  status: Status;
  assignedTo: string;
};

export const STATUSES: Status[] = ['New', 'Contacted', 'Won', 'Lost'];
export const CONFIDENCES: Confidence[] = ['High', 'Medium', 'Low'];
