import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { AnnotateImage } from "./AnnotateImage";
import { kindLabel } from "./KindIcon";
import { formatBytes, isImageItem, orderRounds, visualReviewUrls, type GalleryItem } from "./model";
import { RoundCompare } from "./RoundCompare";
import { HtmlViewer, ImageViewer, PdfViewer, SvgViewer } from "./viewers";

/** The right viewer for one item. */
export function ItemViewer({ item }: { item: GalleryItem }) {
  switch (item.kind) {
    case "image":
    case "screenshot":
      return <ImageViewer src={item.url} alt={item.name} className="max-h-[65vh] max-w-full rounded-md border border-border" />;
    case "svg":
      return <SvgViewer src={item.url} alt={item.name} className="max-h-[65vh] max-w-full rounded-md border border-border bg-background" />;
    case "html":
      return <HtmlViewer src={item.url} title={item.name} className="h-[65vh] w-full rounded-md border border-border bg-background" />;
    case "pdf":
      return <PdfViewer src={item.url} title={item.name} className="h-[65vh] w-full rounded-md border border-border" />;
    default:
      return (
        <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary underline">
          Open {item.name}
        </a>
      );
  }
}

/** Dialog with View, Compare rounds and Annotate tabs for one artifact. */
export function ArtifactViewer({
  runId,
  item,
  allItems,
  onClose,
}: {
  runId: string;
  item: GalleryItem | null;
  allItems: readonly GalleryItem[];
  onClose: () => void;
}) {
  const rounds = item?.taskId ? orderRounds(allItems, item.taskId) : [];
  const canCompare = item !== null && item.taskId !== null && isImageItem(item);
  const review = item?.taskId ? visualReviewUrls(runId, item.taskId) : null;

  return (
    <Dialog open={item !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
        {item && (
          <>
            <DialogHeader>
              <DialogTitle className="break-all pr-6 text-base">{item.name}</DialogTitle>
              <DialogDescription>
                {kindLabel(item.kind)} · {formatBytes(item.size)}
                {item.role ? ` · ${item.role}` : ""}
                {item.taskId ? ` · card ${item.taskId}` : ""}
                {" · "}
                <a href={`${item.url}?download=1`} download={item.name}
                   aria-label={`Download ${item.name}`} className="text-primary underline">
                  Download
                </a>
              </DialogDescription>
            </DialogHeader>
            <Tabs defaultValue="view">
              <TabsList>
                <TabsTrigger value="view">View</TabsTrigger>
                {canCompare && <TabsTrigger value="compare">Compare rounds</TabsTrigger>}
                {isImageItem(item) && <TabsTrigger value="annotate">Annotate</TabsTrigger>}
              </TabsList>
              <TabsContent value="view">
                <ItemViewer item={item} />
              </TabsContent>
              {canCompare && review && (
                <TabsContent value="compare">
                  <RoundCompare rounds={rounds} diffUrl={review.diff} baselineUrl={review.baseline} />
                </TabsContent>
              )}
              {isImageItem(item) && (
                <TabsContent value="annotate">
                  <AnnotateImage runId={runId} item={item} />
                </TabsContent>
              )}
            </Tabs>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
