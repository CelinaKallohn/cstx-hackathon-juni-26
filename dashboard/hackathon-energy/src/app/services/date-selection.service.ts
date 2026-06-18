import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class DateSelectionService {
  private datesSubject = new BehaviorSubject<string[]>([]); // DD.MM.YYYY values
  private indexSubject = new BehaviorSubject<number>(0);
  private dateIsoSubject = new BehaviorSubject<string>(''); // YYYY-MM-DD
  private canPrevSubject = new BehaviorSubject<boolean>(false);
  private canNextSubject = new BehaviorSubject<boolean>(false);

  readonly dates$ = this.datesSubject.asObservable();
  readonly index$ = this.indexSubject.asObservable();
  readonly date$ = this.dateIsoSubject.asObservable();
  readonly canPrev$ = this.canPrevSubject.asObservable();
  readonly canNext$ = this.canNextSubject.asObservable();

   setAvailableDates(dates: string[]) {
     this.datesSubject.next(dates);
     if (dates.length > 0) {
       const idx = Math.max(0, dates.length - 1);
       this.setSelectedIndex(idx);
     } else {
       this.indexSubject.next(0);
       this.dateIsoSubject.next('');
       this.updateNavigationState();
     }
   }

   setSelectedIndex(i: number) {
     const dates = this.datesSubject.getValue();
     const idx = Math.max(0, Math.min(i, Math.max(0, dates.length - 1)));
     this.indexSubject.next(idx);
     const dd = dates[idx];
     if (dd) this.dateIsoSubject.next(this.toIso(dd));
     this.updateNavigationState();
   }

   setDateIso(iso: string) {
     // try to find index for the iso in known dates; otherwise just set iso
     const dates = this.datesSubject.getValue();
     const dd = this.fromIso(iso);
     const idx = dates.indexOf(dd);
     if (idx !== -1) {
       this.setSelectedIndex(idx);
     } else {
       this.dateIsoSubject.next(iso);
       this.updateNavigationState();
     }
   }

   prev() {
      const currentIdx = this.indexSubject.getValue();
      const dates = this.datesSubject.getValue();
      if (dates.length === 0) return;
      const newIdx = Math.max(0, currentIdx - 1);
      this.setSelectedIndex(newIdx);
    }

    next() {
      const currentIdx = this.indexSubject.getValue();
      const dates = this.datesSubject.getValue();
      if (dates.length === 0) return;
      const newIdx = Math.min(dates.length - 1, currentIdx + 1);
      this.setSelectedIndex(newIdx);
    }

   private updateNavigationState() {
     const idx = this.indexSubject.getValue();
     const dates = this.datesSubject.getValue();
     this.canPrevSubject.next(idx > 0);
     this.canNextSubject.next(idx < dates.length - 1);
   }

   private toIso(ddmmyyyy: string): string {
    const parts = ddmmyyyy.split('.');
    if (parts.length !== 3) return '';
    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
  }

  private fromIso(iso: string): string {
    const p = iso.split('-');
    if (p.length !== 3) return '';
    return `${p[2].padStart(2, '0')}.${p[1].padStart(2, '0')}.${p[0]}`;
  }
}

