import { ReactNode } from "react";

/* The body of a page under the page bar. The bar already says where you are,
   so this carries the heading and one line on what the page is for. */
export default function Page({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
  /* Accepted for older call sites; the page bar now carries the location. */
  eyebrow?: string;
}) {
  return (
    <div className="content">
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <p className="page-description">{description}</p>
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}
